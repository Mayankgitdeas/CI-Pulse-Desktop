"""
scraper_playwright.py — Playwright-based web scraper for dynamic news sites

Handles JavaScript-rendered pages that sitemap/HTTP scraping can't reach.
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

# Playwright is imported lazily inside the scrape function so the app can
# start (and serve the dashboard) even on hosts where Playwright/Chromium
# isn't installed or can't run (e.g. low-memory free tiers).
log = logging.getLogger(__name__)


@dataclass
class ScrapedArticle:
    competitor: str
    url: str
    headline: str
    description: str
    published_at: datetime
    source: str
    source_priority: int = 1
    suggested_industry: Optional[str] = None
    suggested_tag: Optional[str] = None
    suggested_topics: Optional[list] = None
    suggested_impact: str = "med"


@dataclass
class ScrapeResult:
    articles: list
    failed_sources: list
    duration_seconds: float


async def scrape_newsroom_playwright(url: str, competitor: str, source_name: str, cutoff: datetime, source_priority: int = 1) -> list:
    """Scrape a newsroom page using Playwright (renders JavaScript)."""
    
    articles = []
    try:
        from playwright.async_api import async_playwright  # lazy import
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
                      '--disable-gpu', '--disable-blink-features=AutomationControlled']
            )
            # Real browser user-agent so sites don't block/stall the headless browser
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1366, 'height': 768},
            )
            page = await context.new_page()
            # Block images/fonts/media to load faster (we only need the HTML links)
            await page.route("**/*", lambda route: route.abort()
                             if route.request.resource_type in ("image", "font", "media") else route.continue_())

            # Navigate with retry. "commit" = fire as soon as the server responds,
            # don't wait for all sub-resources (corporate sites have endless trackers).
            loaded = False
            for attempt in range(2):
                try:
                    await page.goto(url, wait_until="commit", timeout=60000)
                    await page.wait_for_timeout(3500)  # let JS render the article list
                    loaded = True
                    break
                except Exception as e:
                    log.warning(f"{source_name} attempt {attempt+1} failed: {str(e)[:80]}")
                    await page.wait_for_timeout(1500)
            if not loaded:
                log.error(f"Playwright could not load {source_name} after retries")
                await browser.close()
                return []
            
            # Find article links — broad patterns covering most newsroom structures
            selectors = [
                'a[href*="/news"]', 'a[href*="/press"]', 'a[href*="/article"]',
                'a[href*="newsroom"]', 'a[href*="/media"]', 'a[href*="/insights"]',
                'a[href*="/2026"]', 'a[href*="/2025"]', 'article a', '.news-item a',
                '.card a', 'h2 a', 'h3 a'
            ]
            
            seen_urls = set()
            for selector in selectors:
                links = await page.query_selector_all(selector)
                for link in links:
                    try:
                        href = await link.get_attribute('href')
                        if not href or '#' in href or 'javascript:' in href:
                            continue
                        
                        # Make absolute URL
                        if href.startswith('/'):
                            from urllib.parse import urljoin
                            href = urljoin(url, href)
                        elif not href.startswith('http'):
                            continue
                        
                        # Skip duplicates and non-article pages
                        if href in seen_urls:
                            continue
                        # Skip obvious non-articles (nav, category pages)
                        skip_patterns = ['/category/', '/tag/', '/author/', '/page/', 
                                        'twitter.com', 'facebook.com', 'linkedin.com',
                                        '/about', '/contact', '/careers', '/privacy']
                        if any(p in href.lower() for p in skip_patterns):
                            continue
                        
                        headline = (await link.text_content() or '').strip()
                        if not headline or len(headline) < 20 or len(headline) > 200:
                            continue
                        
                        seen_urls.add(href)
                        
                        articles.append(ScrapedArticle(
                            competitor=competitor,
                            url=href,
                            headline=headline,
                            description=headline,
                            published_at=datetime.now(timezone.utc),
                            source=source_name,
                            source_priority=source_priority,
                            suggested_industry="Cross-industry",
                            suggested_tag=None,
                            suggested_topics=[],
                            suggested_impact="med",
                        ))
                        
                        if len(articles) >= 15:  # Cap per source
                            break
                    except Exception as e:
                        log.debug(f"Link parse error: {e}")
                        continue
                
                if len(articles) >= 15:
                    break
            
            await browser.close()
            
    except Exception as e:
        log.error(f"Playwright scrape failed for {source_name}: {e}")
    
    return articles


async def scrape_all_playwright(sources: list, cutoff: datetime) -> ScrapeResult:
    """Scrape all enabled sources using Playwright."""
    
    start = datetime.now(timezone.utc)
    
    # Limit concurrent browsers to 5 to avoid memory issues
    semaphore = asyncio.Semaphore(3)
    
    async def scrape_with_limit(src):
        async with semaphore:
            return await scrape_newsroom_playwright(src.url, src.competitor, src.name, cutoff, src.priority)
    
    tasks = [scrape_with_limit(src) for src in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_articles = []
    failed = []
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            failed.append({"source": sources[i].name, "error": str(result)[:200]})
            log.error(f"  {sources[i].name}: FAILED - {result}")
        else:
            all_articles.extend(result)
            log.info(f"  {sources[i].name}: {len(result)} articles")
    
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    return ScrapeResult(articles=all_articles, failed_sources=failed, duration_seconds=duration)
