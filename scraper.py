"""
scraper.py — Competitor newsroom scrapers

Fetches recent articles from 5 P1 competitor newsrooms using a multi-strategy
approach: tries sitemap.xml first (works for JS-rendered SPA newsrooms),
falls back to HTML listing parse if sitemap fails.

LEGAL NOTE: Scraping public newsroom pages is a gray area. This implementation
is for INTERNAL Cognizant use only. Cognizant Legal should review before any
pilot rollout beyond your laptop.

Mitigation measures:
  - Triggered ONLY by admin click (no continuous crawling)
  - Single request per source per scrape
  - Identifying User-Agent
  - Captures only enough text to identify signals
"""

import asyncio
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────

USER_AGENT = "Cognizant CI Pulse Bot (Internal Competitive Intelligence Tool)"
REQUEST_TIMEOUT = 25.0
MAX_ARTICLES_PER_SOURCE = 20  # accept up to 20 candidates from sitemap (we'll filter)
ARTICLE_FETCH_LIMIT = 15      # fetch up to 15 article pages for real date + metadata


# ─── DATA TYPES ──────────────────────────────────────────────────────────────

@dataclass
class ScrapedArticle:
    competitor: str
    url: str
    headline: str
    description: str
    published_at: datetime
    source: str
    source_priority: int = 1
    suggested_industry: Optional[str] = None  # v0.4: new single-pick taxonomy
    suggested_tag: Optional[str] = None       # legacy; kept for backward compat
    suggested_topics: Optional[list] = None
    suggested_impact: str = "med"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d


@dataclass
class ScrapeResult:
    articles: list
    failed_sources: list
    duration_seconds: float

    def to_dict(self) -> dict:
        return {
            "articles": [a.to_dict() for a in self.articles],
            "failed_sources": self.failed_sources,
            "count": len(self.articles),
            "duration_seconds": round(self.duration_seconds, 2),
        }


# ─── CLASSIFICATION HEURISTICS ───────────────────────────────────────────────

# ─── CLASSIFICATION (v0.4 taxonomy) ───────────────────────────────────────────
# INDUSTRY_KEYWORDS: single-pick. First match in dict-iteration order wins.
# TOPIC_KEYWORDS: multi-pick. Combined tech areas + news categories. Frontend
# splits them visually using the lists in models.py.

INDUSTRY_KEYWORDS = {
    "BFSI":                      ["bank", "banking", "financial services", "fintech", "capital markets", "wealth", "lending", "credit card"],
    "Insurance":                 ["insurance", "insurer", "underwriter", "actuarial", "claims process"],
    "Life Sciences":             ["pharma", "biotech", "clinical trial", "drug discovery", "life sciences", "lifesciences"],
    "Healthcare":                ["healthcare", "hospital", "patient", "clinical", "medical", "klas"],
    "Manufacturing":             ["manufacturing", "industrial", "factory", "supply chain", "automotive", "aerospace", "siemens"],
    "Retail & CPG":              ["retail", "ecommerce", "e-commerce", "consumer goods", "cpg", "store", "shopper", "merchant"],
    "Telecom":                   ["telecom", "telco", "telecommunications", "5g network", "mobile operator", "carrier"],
    "Energy & Utilities":        ["energy", "utilities", "oil and gas", "renewable", "grid", "power generation"],
    "Public Sector":             ["public sector", "government", "federal", "ministry", "agency contract"],
    "Communications & Media":    ["media company", "broadcast", "publishing", "entertainment", "streaming service"],
    "Travel & Hospitality":      ["airline", "hotel", "hospitality", "tourism", "travel industry"],
}

# Combined tech + news topics. Multi-pick.
TOPIC_KEYWORDS = {
    # Tech topics
    "AI & GenAI":          ["ai", "artificial intelligence", "generative", "genai", "gen ai", "machine learning", "agentic", "llm", "openai", "anthropic", "claude"],
    "Cloud":               ["cloud", "azure", "aws", "amazon web services", "google cloud", "gcp", "cloud migration"],
    "Data & Analytics":    ["data platform", "data lake", "data warehouse", "analytics platform", "insights platform", "snowflake", "databricks"],
    "Cybersecurity":       ["cybersecurity", "cyber attack", "ransomware", "data breach", "siem", "zero trust", "soc service"],
    "Sustainability":      ["sustainability", "esg", "net zero", "carbon", "green energy"],
    "Quantum":             ["quantum computing", "quantum-safe"],
    "Edge / IoT":          ["iot", "edge computing", "edge ai"],
    "Platform / SaaS":     ["saas platform", "platform-as-a-service", "paas"],
    # News-category topics
    "Contract win":        ["wins", "deal", "selects", "selected to", "engagement", "transformation engagement", "secures", "awarded contract"],
    "Partnership":         ["partnership", "partners with", "alliance", "collaboration", "joint venture", "strategic agreement", "strategic collaboration"],
    "M&A":                 ["acquisition", "acquires", "acquired", "merger", "completed the acquisition", "to acquire"],
    "Leadership change":   ["appoints", "names", "elects", "new ceo", "new cfo", "new cto", "chief executive", "leadership change", "new leadership"],
    "Product launch":      ["launches", "unveils", "introduces", "announces availability", "release of", "new offering"],
    "Earnings":            ["earnings", "quarterly results", "q1 results", "q2 results", "q3 results", "q4 results", "annual results", "revenue grew"],
    "Layoff / restructure": ["layoff", "layoffs", "restructure", "restructuring", "job cuts", "workforce reduction"],
    "Regulatory":          ["regulator", "compliance order", "antitrust", "sec filing", "investigation by"],
    "IP / patent":         ["patent", "intellectual property", "trademark filing"],
    "Investment / funding": ["funding round", "raises", "series ", "ipo", "valuation"],
    "Strategy / direction": ["strategy", "roadmap", "five-year plan", "transformation strategy"],
}

# Which topics are "news" (single-pick semantically) for legacy `tag` derivation
_NEWS_TOPIC_SET = {
    "Contract win", "Partnership", "M&A", "Leadership change", "Product launch",
    "Earnings", "Layoff / restructure", "Regulatory", "IP / patent",
    "Investment / funding", "Strategy / direction",
}

IMPACT_HIGH_KEYWORDS = [
    "billion", "$1b", "8-year", "10-year", "multi-year", "largest", "strategic transformation",
    "first-of-its-kind", "first of its kind", "industry first", "global partnership",
    "completes acquisition", "to acquire", "named partner of the year",
]
IMPACT_LOW_KEYWORDS = [
    "esop", "stock option", "share allotment", "annual report", "dividend",
    "board meeting", "investor presentation", "calendar",
]


def suggest_classification(headline: str, description: str) -> dict:
    """Classify an article into the new taxonomy.
    Returns: industry (single str), topics (list, mixed tech+news), tag (legacy
    backward-compat — first news topic match), impact ('hi'/'med'/'lo')."""
    text = (headline + " " + description).lower()

    # Industry: first match wins
    industry = None
    for ind, kws in INDUSTRY_KEYWORDS.items():
        if any(kw in text for kw in kws):
            industry = ind
            break
    if not industry:
        industry = "Cross-industry"

    # Topics: collect all matches (tech + news)
    topics = [t for t, kws in TOPIC_KEYWORDS.items() if any(kw in text for kw in kws)]

    # Legacy `tag` field for backward compat: first news topic encountered
    legacy_tag = None
    for t in topics:
        if t in _NEWS_TOPIC_SET:
            legacy_tag = t
            break

    # Impact
    impact = "med"
    if any(kw in text for kw in IMPACT_HIGH_KEYWORDS):
        impact = "hi"
    elif any(kw in text for kw in IMPACT_LOW_KEYWORDS):
        impact = "lo"

    return {"industry": industry, "tag": legacy_tag, "topics": topics, "impact": impact}


# ─── HTTP & PARSING HELPERS ──────────────────────────────────────────────────

async def fetch_text(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Fetch URL and return text. Returns None on failure."""
    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {type(e).__name__}")
        return None


def parse_sitemap_xml(xml_text: str, url_pattern: re.Pattern, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """Parse a sitemap.xml string and return [(url, lastmod_datetime), ...]
    filtering by url_pattern (regex) and date range [cutoff, upper]."""
    results = []
    try:
        cleaned = re.sub(r'\sxmlns="[^"]+"', '', xml_text, count=1)
        root = ET.fromstring(cleaned)
    except ET.ParseError as e:
        log.warning(f"Sitemap XML parse error: {e}")
        return results

    for url_elem in root.findall(".//url"):
        loc = url_elem.findtext("loc", "").strip()
        lastmod_str = url_elem.findtext("lastmod", "").strip()
        if not loc or not url_pattern.search(loc):
            continue
        lastmod = None
        if lastmod_str:
            try:
                lastmod = datetime.fromisoformat(lastmod_str.replace("Z", "+00:00"))
                if lastmod.tzinfo is None:
                    lastmod = lastmod.replace(tzinfo=timezone.utc)
            except ValueError:
                try:
                    lastmod = datetime.strptime(lastmod_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
        if lastmod and lastmod >= cutoff:
            if upper is None or lastmod <= upper:
                results.append((loc, lastmod))
    return results


def parse_sitemap_index(xml_text: str) -> list:
    """If the fetched sitemap is an index (<sitemapindex>), return list of child sitemap URLs."""
    try:
        cleaned = re.sub(r'\sxmlns="[^"]+"', '', xml_text, count=1)
        root = ET.fromstring(cleaned)
        if root.tag != "sitemapindex":
            return []
        return [sm.findtext("loc", "").strip() for sm in root.findall(".//sitemap") if sm.findtext("loc")]
    except ET.ParseError:
        return []


async def fetch_article_metadata(url: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Fetch an article page and extract title, description, and real publish date.

    Returns {'title': str, 'description': str, 'published_at': datetime | None}.
    For published_at we try (in order):
      1. <meta property="article:published_time">
      2. <meta name="publish_date">, name="pubdate", name="date"
      3. <meta itemprop="datePublished">
      4. <time datetime="...">
      5. JSON-LD <script type="application/ld+json"> datePublished
      6. None (caller decides what to do)
    """
    html = await fetch_text(url, client)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # Title — try og:title first, fall back to <title>
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    elif soup.title:
        title = soup.title.get_text(strip=True)

    # Description
    desc = ""
    og_d = soup.find("meta", property="og:description")
    if og_d and og_d.get("content"):
        desc = og_d["content"].strip()
    else:
        meta_d = soup.find("meta", attrs={"name": "description"})
        if meta_d and meta_d.get("content"):
            desc = meta_d["content"].strip()

    # ── Real publish date — try multiple strategies ────────────────────────
    published = None

    # Strategy 1: article:published_time meta tag (most common, Open Graph)
    pt = soup.find("meta", property="article:published_time")
    if pt and pt.get("content"):
        published = _parse_iso_date(pt["content"])

    # Strategy 2: alternative meta date tags
    if not published:
        for attr_name in ["publish_date", "pubdate", "date", "DC.date.issued", "dcterms.created"]:
            m = soup.find("meta", attrs={"name": attr_name})
            if m and m.get("content"):
                published = _parse_iso_date(m["content"])
                if published:
                    break

    # Strategy 3: itemprop datePublished
    if not published:
        ip = soup.find(attrs={"itemprop": "datePublished"})
        if ip:
            content = ip.get("content") or ip.get("datetime") or ip.get_text(strip=True)
            published = _parse_iso_date(content)

    # Strategy 4: <time datetime="...">
    if not published:
        for t in soup.find_all("time"):
            dt_attr = t.get("datetime")
            if dt_attr:
                published = _parse_iso_date(dt_attr)
                if published:
                    break

    # Strategy 5: JSON-LD structured data (used by many corporate sites)
    if not published:
        published = _extract_jsonld_date(soup)

    # Strategy 6: fall back to body text — look for "May 12, 2026" etc near top
    if not published:
        body_text = soup.get_text(" ", strip=True)[:3000]  # first 3000 chars
        published = _extract_date_from_text(body_text)

    return {"title": title, "description": desc, "published_at": published}


def _parse_iso_date(s: str) -> Optional[datetime]:
    """Parse an ISO-like date string into a UTC datetime, or None."""
    if not s:
        return None
    s = s.strip()
    try:
        # Handle "Z" timezone marker
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except ValueError:
        pass
    # Try date-only (YYYY-MM-DD)
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_jsonld_date(soup) -> Optional[datetime]:
    """Pull datePublished out of any <script type='application/ld+json'> block."""
    import json
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        # data may be dict or list of dicts; walk it
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            for key in ("datePublished", "dateCreated", "uploadDate"):
                if key in item:
                    d = _parse_iso_date(str(item[key]))
                    if d:
                        return d
    return None


def _extract_date_from_text(text: str) -> Optional[datetime]:
    """Last-ditch: look for 'May 12, 2026' or similar in article body text."""
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    # Pattern: "May 12, 2026" or "12 May 2026"
    pattern1 = re.compile(r"\b([A-Z][a-z]{2,9})\s+(\d{1,2}),?\s+(\d{4})\b")
    pattern2 = re.compile(r"\b(\d{1,2})\s+([A-Z][a-z]{2,9})\s+(\d{4})\b")
    for pattern, order in [(pattern1, "mdy"), (pattern2, "dmy")]:
        match = pattern.search(text)
        if match:
            try:
                if order == "mdy":
                    month_name, day, year = match.group(1), match.group(2), match.group(3)
                else:
                    day, month_name, year = match.group(1), match.group(2), match.group(3)
                month = months.get(month_name.lower())
                if month:
                    return datetime(int(year), month, int(day), 12, 0, 0, tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
    return None


async def scrape_via_sitemap(
    competitor: str,
    sitemap_urls: list,
    article_pattern: re.Pattern,
    source_name: str,
    cutoff: datetime,
    client: httpx.AsyncClient,
    upper: Optional[datetime] = None,
    source_priority: int = 1,
) -> list:
    """Generic sitemap-based scraper with multi-URL fallback.

    Tries each sitemap URL in order. For each one, also follows sitemap indexes
    and prioritizes child sitemaps containing 'news' or 'press' in their URL.
    Returns articles from the FIRST sitemap URL that yields matching results.
    """
    all_urls = []

    for sitemap_url in sitemap_urls:
        xml = await fetch_text(sitemap_url, client)
        if not xml:
            continue

        # Check if it's a sitemap index (points to child sitemaps)
        child_sitemaps = parse_sitemap_index(xml)
        if child_sitemaps:
            # Prioritize child sitemaps that look like news/press sitemaps
            def priority(url):
                u = url.lower()
                if "press" in u or "news" in u or "newsroom" in u:
                    return 0  # highest priority
                if "blog" in u or "article" in u:
                    return 1
                return 2
            child_sitemaps.sort(key=priority)

            # Follow up to 8 child sitemaps (more than before)
            for child_url in child_sitemaps[:8]:
                child_xml = await fetch_text(child_url, client)
                if child_xml:
                    matches = parse_sitemap_xml(child_xml, article_pattern, cutoff, upper)
                    if matches:
                        log.info(f"  {competitor}: found {len(matches)} candidates in {child_url}")
                        all_urls.extend(matches)
        else:
            # Direct sitemap (no index)
            matches = parse_sitemap_xml(xml, article_pattern, cutoff, upper)
            if matches:
                log.info(f"  {competitor}: found {len(matches)} candidates in {sitemap_url}")
                all_urls.extend(matches)

        # If we found enough candidates, stop trying more sitemap URLs
        if len(all_urls) >= MAX_ARTICLES_PER_SOURCE:
            break

    if not all_urls:
        log.warning(f"  {competitor}: no articles found across {len(sitemap_urls)} sitemap candidate(s)")
        return []

    # Dedupe by URL (multiple sitemaps may list the same article)
    seen = set()
    unique = []
    for url, lastmod in all_urls:
        if url not in seen:
            seen.add(url)
            unique.append((url, lastmod))
    all_urls = unique

    # Sort by lastmod desc, take top N
    all_urls.sort(key=lambda x: x[1], reverse=True)
    all_urls = all_urls[:MAX_ARTICLES_PER_SOURCE]

    # Fetch article metadata for top results (in parallel)
    articles = []
    metadata_tasks = [fetch_article_metadata(url, client) for url, _ in all_urls[:ARTICLE_FETCH_LIMIT]]
    metadatas = await asyncio.gather(*metadata_tasks, return_exceptions=True)

    skipped_no_date = 0
    skipped_out_of_range = 0

    for (url, lastmod), meta in zip(all_urls[:ARTICLE_FETCH_LIMIT], metadatas):
        if isinstance(meta, Exception) or not meta:
            slug = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "").replace("-", " ").title()
            title = slug
            desc = slug
            article_date = None
        else:
            title = meta.get("title", "") or url.rsplit("/", 1)[-1]
            desc = meta.get("description", "") or title
            article_date = meta.get("published_at")

        if not title or len(title) < 15:
            continue

        # TRULY STRICT filtering — require real publish date AND in range.
        # If we can't determine the real publish date, discard. This is the
        # correct behavior even though it loses articles where metadata
        # extraction failed — better to miss articles than to lie about dates.
        if article_date is None:
            skipped_no_date += 1
            log.debug(f"  skip no-real-date: {competitor} {url}")
            continue

        if article_date < cutoff or (upper is not None and article_date > upper):
            skipped_out_of_range += 1
            log.debug(
                f"  skip out-of-range: {competitor} {url} "
                f"(real={article_date.date()}, range={cutoff.date()}..{upper.date() if upper else 'now'})"
            )
            continue

        suggestion = suggest_classification(title, desc)
        articles.append(ScrapedArticle(
            competitor=competitor,
            url=url,
            headline=title[:300],
            description=desc[:500] if desc else title[:300],
            published_at=article_date,  # always the real date
            source=source_name,
            source_priority=source_priority,
            suggested_industry=suggestion["industry"],
            suggested_tag=suggestion["tag"],
            suggested_topics=suggestion["topics"],
            suggested_impact=suggestion["impact"],
        ))

    if skipped_out_of_range or skipped_no_date:
        log.info(
            f"  {competitor}: filtered out {skipped_out_of_range} out-of-range, "
            f"{skipped_no_date} no-real-date"
        )
    return articles


# ─── PER-COMPETITOR SCRAPERS ─────────────────────────────────────────────────
# Each provides a list of candidate sitemap URLs to try in order.
# This handles companies that split their sitemap across multiple files,
# typically by section (newsroom, blog, marketing pages, etc.)

async def scrape_accenture(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """Accenture: dedicated newsroom subdomain — should have one main sitemap."""
    sitemaps = [
        "https://newsroom.accenture.com/sitemap.xml",
        "https://newsroom.accenture.com/sitemap_index.xml",
        "https://newsroom.accenture.com/news-sitemap.xml",
    ]
    pattern = re.compile(r"newsroom\.accenture\.com/news/\d{4}/")
    return await scrape_via_sitemap("Accenture", sitemaps, pattern, "Accenture Newsroom", cutoff, client, upper)


async def scrape_tcs(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """TCS: sitemap-based, with HTML fallback."""
    sitemaps = [
        "https://www.tcs.com/sitemap.xml",
        "https://www.tcs.com/sitemap_index.xml",
    ]
    pattern = re.compile(r"/who-we-are/newsroom/press-release/")
    results = await scrape_via_sitemap("TCS", sitemaps, pattern, "TCS Newsroom", cutoff, client, upper)
    if results:
        return results
    return await _scrape_tcs_html(client, cutoff)


async def _scrape_tcs_html(client: httpx.AsyncClient, cutoff: datetime) -> list:
    """Original TCS HTML scraper as last-resort fallback."""
    url = "https://www.tcs.com/who-we-are/newsroom"
    html = await fetch_text(url, client)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/newsroom/press-release/" not in href:
            continue
        full_url = urljoin(url, href)
        title = link.get_text(strip=True)
        if not title or len(title) < 20:
            continue
        published = datetime.now(timezone.utc)
        if published < cutoff:
            continue
        suggestion = suggest_classification(title, "")
        articles.append(ScrapedArticle(
            competitor="TCS", url=full_url,
            headline=title[:300], description=title[:500],
            published_at=published, source="TCS Newsroom", source_priority=1,
            suggested_industry=suggestion["industry"], suggested_tag=suggestion["tag"], suggested_topics=suggestion["topics"], suggested_impact=suggestion["impact"],
        ))
        if len(articles) >= MAX_ARTICLES_PER_SOURCE:
            break
    return articles


async def scrape_infosys(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """Infosys: try multiple sitemap URLs."""
    sitemaps = [
        "https://www.infosys.com/sitemap.xml",
        "https://www.infosys.com/sitemap_index.xml",
        "https://www.infosys.com/newsroom/sitemap.xml",
    ]
    pattern = re.compile(r"/newsroom/press-releases/\d{4}/[^/]+\.html")
    return await scrape_via_sitemap("Infosys", sitemaps, pattern, "Infosys Newsroom", cutoff, client, upper)


async def scrape_wipro(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """Wipro: main sitemap.xml mostly has case-studies, press releases are
    likely in a sitemap index or sub-sitemap. Try multiple URLs."""
    sitemaps = [
        "https://www.wipro.com/sitemap-index.xml",   # try index first
        "https://www.wipro.com/sitemap_index.xml",
        "https://www.wipro.com/sitemap.xml",
        "https://www.wipro.com/newsroom/sitemap.xml",
        "https://www.wipro.com/sitemap-news.xml",
    ]
    pattern = re.compile(r"/newsroom/press-releases/\d{4}/")
    return await scrape_via_sitemap("Wipro", sitemaps, pattern, "Wipro Newsroom", cutoff, client, upper)


async def scrape_capgemini(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """Capgemini: WordPress 6.9.4 site with locale-prefixed URLs.
    Try WordPress sitemap (wp-sitemap.xml) first, then locale-specific sitemaps,
    then fall back to HTML scraping of the press-releases listing page.
    """
    sitemaps = [
        "https://www.capgemini.com/us-en/wp-sitemap.xml",   # WordPress 5.5+ native
        "https://www.capgemini.com/wp-sitemap.xml",
        "https://www.capgemini.com/sitemap_index.xml",
        "https://www.capgemini.com/sitemap.xml",
        "https://www.capgemini.com/us-en/sitemap.xml",
    ]
    pattern = re.compile(r"/news/press-releases/")
    results = await scrape_via_sitemap(
        "Capgemini", sitemaps, pattern, "Capgemini Newsroom", cutoff, client, upper
    )
    if results:
        return results
    # HTML fallback: scrape the press-releases listing page directly
    log.info("  Capgemini: sitemap empty, trying HTML listing fallback")
    return await _scrape_capgemini_html(client, cutoff, upper)


async def _scrape_capgemini_html(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """HTML fallback for Capgemini — scrape press releases listing page directly.
    Pulls article links matching /us-en/news/press-releases/<slug>/ pattern,
    then fetches each for real publish date and metadata."""
    listing_urls = [
        "https://www.capgemini.com/us-en/news/press-releases/",
        "https://www.capgemini.com/us-en/news/",
    ]
    article_link_pattern = re.compile(r"/us-en/news/press-releases/[a-z0-9][a-z0-9-]+/?$")
    seen = set()
    candidate_urls = []

    for listing_url in listing_urls:
        html = await fetch_text(listing_url, client)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if not article_link_pattern.search(href):
                continue
            full_url = urljoin(listing_url, href)
            # Skip the listing page itself
            if full_url.rstrip("/") == listing_url.rstrip("/"):
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            candidate_urls.append(full_url)
            if len(candidate_urls) >= MAX_ARTICLES_PER_SOURCE:
                break
        if candidate_urls:
            break  # found articles, no need to try other listing URLs

    if not candidate_urls:
        log.warning("  Capgemini: HTML fallback also found no articles")
        return []

    log.info(f"  Capgemini: HTML fallback found {len(candidate_urls)} candidate articles")

    # Fetch metadata for each in parallel — strict date filter
    metadata_tasks = [fetch_article_metadata(u, client) for u in candidate_urls[:ARTICLE_FETCH_LIMIT]]
    metadatas = await asyncio.gather(*metadata_tasks, return_exceptions=True)

    articles = []
    for url, meta in zip(candidate_urls[:ARTICLE_FETCH_LIMIT], metadatas):
        if isinstance(meta, Exception) or not meta:
            continue
        title = meta.get("title", "")
        desc = meta.get("description", "") or title
        article_date = meta.get("published_at")
        if not title or len(title) < 15 or article_date is None:
            continue
        if article_date < cutoff or (upper is not None and article_date > upper):
            continue
        suggestion = suggest_classification(title, desc)
        articles.append(ScrapedArticle(
            competitor="Capgemini",
            url=url,
            headline=title[:300],
            description=desc[:500],
            published_at=article_date,
            source="Capgemini Newsroom",
            source_priority=1,
            suggested_industry=suggestion["industry"],
            suggested_tag=suggestion["tag"],
            suggested_topics=suggestion["topics"],
            suggested_impact=suggestion["impact"],
        ))
    return articles


# ─── P2 SCRAPERS — News portals covering all 5 competitors ──────────────────
# These scrape per-competitor tag/topic pages on news websites.
# Each scrapes 5 tag pages (one per Cognizant competitor).

P2_COMPETITORS = ["accenture", "tcs", "infosys", "wipro", "capgemini"]

# Map of URL-friendly slugs to display names (case-corrected)
P2_DISPLAY_NAMES = {
    "accenture": "Accenture",
    "tcs": "TCS",
    "infosys": "Infosys",
    "wipro": "Wipro",
    "capgemini": "Capgemini",
}


async def _scrape_listing_page(
    client: httpx.AsyncClient,
    listing_url: str,
    competitor_display: str,
    source_name: str,
    article_url_pattern: re.Pattern,
    cutoff: datetime,
    upper: Optional[datetime],
) -> list:
    """Generic news-portal listing scraper. Fetches a tag/topic page, parses
    article links, fetches each for metadata + real publish date, returns
    articles filtered by real publish date in [cutoff, upper]."""
    html = await fetch_text(listing_url, client)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    # Find article links matching pattern
    candidate_urls = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not article_url_pattern.search(href):
            continue
        full_url = urljoin(listing_url, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        candidate_urls.append(full_url)
        if len(candidate_urls) >= MAX_ARTICLES_PER_SOURCE:
            break

    if not candidate_urls:
        return []

    # Fetch metadata for each in parallel
    metadata_tasks = [fetch_article_metadata(u, client) for u in candidate_urls[:ARTICLE_FETCH_LIMIT]]
    metadatas = await asyncio.gather(*metadata_tasks, return_exceptions=True)

    articles = []
    for url, meta in zip(candidate_urls[:ARTICLE_FETCH_LIMIT], metadatas):
        if isinstance(meta, Exception) or not meta:
            continue
        title = meta.get("title", "")
        desc = meta.get("description", "") or title
        article_date = meta.get("published_at")
        if not title or len(title) < 15 or article_date is None:
            continue
        if article_date < cutoff or (upper is not None and article_date > upper):
            continue
        # P2 articles: which competitor? — derive from listing URL context (already known)
        suggestion = suggest_classification(title, desc)
        articles.append(ScrapedArticle(
            competitor=competitor_display,
            url=url,
            headline=title[:300],
            description=desc[:500],
            published_at=article_date,
            source=source_name,
            source_priority=2,  # P2 — financial wire / media
            suggested_industry=suggestion["industry"],
            suggested_tag=suggestion["tag"],
            suggested_topics=suggestion["topics"],
            suggested_impact=suggestion["impact"],
        ))
    return articles


async def scrape_livemint(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """Livemint topic pages — one per Cognizant competitor."""
    article_pattern = re.compile(r"livemint\.com/.*-\d+\.html|livemint\.com/companies/news/")
    all_articles = []
    tasks = []
    for slug in P2_COMPETITORS:
        listing = f"https://www.livemint.com/topic/{slug}"
        display = P2_DISPLAY_NAMES[slug]
        tasks.append(_scrape_listing_page(
            client, listing, display, "Livemint", article_pattern, cutoff, upper
        ))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            all_articles.extend(r)
    return all_articles


async def scrape_economic_times(client: httpx.AsyncClient, cutoff: datetime, upper: Optional[datetime] = None) -> list:
    """Economic Times topic pages — one per Cognizant competitor."""
    article_pattern = re.compile(r"economictimes\.indiatimes\.com/.*articleshow/\d+\.cms")
    all_articles = []
    tasks = []
    for slug in P2_COMPETITORS:
        listing = f"https://economictimes.indiatimes.com/topic/{slug}"
        display = P2_DISPLAY_NAMES[slug]
        tasks.append(_scrape_listing_page(
            client, listing, display, "Economic Times", article_pattern, cutoff, upper
        ))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            all_articles.extend(r)
    return all_articles


# ─── ORCHESTRATOR ────────────────────────────────────────────────────────────

# Legacy hardcoded P1 scrapers — used when a DB source name matches a known
# competitor (since these have site-specific HTML parsing logic).
LEGACY_P1_SCRAPERS = {
    "Accenture": scrape_accenture,
    "TCS":       scrape_tcs,
    "Infosys":   scrape_infosys,
    "Wipro":     scrape_wipro,
    "Capgemini": scrape_capgemini,
}

# P2 generic listing patterns — used by _scrape_source_generic when an HTML
# listing source's URL points to a known portal.
_P2_LISTING_PATTERNS = {
    "livemint.com": re.compile(r"livemint\.com/.*-\d+\.html|livemint\.com/companies/news/"),
    "economictimes.indiatimes.com": re.compile(r"economictimes\.indiatimes\.com/.*articleshow/\d+\.cms"),
}


def _outlet_from_url(url: str) -> str:
    """Cheap human-readable outlet label from a URL."""
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


async def _scrape_source_generic(
    client: httpx.AsyncClient,
    src,
    cutoff: datetime,
    upper: Optional[datetime],
) -> list:
    """Generic dispatch for sources that don't have a hardcoded legacy scraper.
    Reads source.scraper_type and competitor to drive _scrape_listing_page or
    scrape_via_sitemap. Used for P2 rows + any new sources added via admin."""
    if src.scraper_type == "html_listing":
        domain = _outlet_from_url(src.url)
        pattern = None
        for key, pat in _P2_LISTING_PATTERNS.items():
            if key in src.url.lower():
                pattern = pat
                break
        if pattern is None:
            # Unknown outlet — try a broad pattern that captures /article|news|/yyyy/...
            pattern = re.compile(r"/(?:news|article|story|press-release)/")
        return await _scrape_listing_page(
            client, src.url, src.competitor, src.name, pattern, cutoff, upper
        )
    elif src.scraper_type == "sitemap":
        # Use existing sitemap-driven scraper. URL pattern is permissive
        # since we don't know the new site's structure; date filtering still applies.
        return await scrape_via_sitemap(
            client=client,
            sitemap_urls=[src.url],
            competitor=src.competitor,
            source_name=src.name,
            article_pattern=re.compile(r".*"),  # accept all article URLs
            cutoff=cutoff,
            upper=upper,
            source_priority=src.priority,
        )
    else:
        log.warning(f"Unknown scraper_type '{src.scraper_type}' for source {src.name} (skipping)")
        return []


async def scrape_all(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    days_window: int = 14,
) -> ScrapeResult:
    """Scrape all enabled sources from the DB.

    Loads Source rows where enabled=True. Dispatches each by name:
    - Legacy P1 names (Accenture/TCS/Infosys/Wipro/Capgemini) → hardcoded scraper
    - Everything else → _scrape_source_generic

    from_date: lower bound (inclusive); defaults to today - days_window
    to_date:   upper bound (inclusive); defaults to None (no upper bound)
    """
    start = datetime.now(timezone.utc)
    if from_date:
        cutoff = from_date if from_date.tzinfo else from_date.replace(tzinfo=timezone.utc)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_window)
    upper = to_date if (to_date is None or to_date.tzinfo) else (to_date.replace(tzinfo=timezone.utc) if to_date else None)

    log.info(f"Scrape range: {cutoff.isoformat()} → {(upper.isoformat() if upper else 'now')}")

    # Load enabled sources from DB
    from database import get_session
    from models import Source
    from sqlmodel import select as _select
    with get_session() as s:
        sources = s.exec(
            _select(Source).where(Source.enabled == True).order_by(Source.priority, Source.name)
        ).all()
    log.info(f"Scraping {len(sources)} enabled source(s)")

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        tasks = {}
        for src in sources:
            # P1 legacy competitors use their hardcoded scraper (has site-specific knowledge)
            if src.priority == 1 and src.name in LEGACY_P1_SCRAPERS:
                fn = LEGACY_P1_SCRAPERS[src.name]
                tasks[src.name] = asyncio.create_task(fn(client, cutoff, upper))
            else:
                tasks[src.name] = asyncio.create_task(_scrape_source_generic(client, src, cutoff, upper))

        all_articles = []
        failed = []
        for name, task in tasks.items():
            try:
                articles = await task
                all_articles.extend(articles)
                log.info(f"  {name}: {len(articles)} articles")
            except Exception as e:
                log.error(f"  {name} scrape FAILED: {e}")
                failed.append({"source": name, "error": str(e)[:200]})

    duration = (datetime.now(timezone.utc) - start).total_seconds()
    return ScrapeResult(articles=all_articles, failed_sources=failed, duration_seconds=duration)
