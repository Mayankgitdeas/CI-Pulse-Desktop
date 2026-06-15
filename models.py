"""
models.py — Data models

The Signal is the core entity. Notice the four `impact_*` fields — these
match the four template questions the analyst fills in. The frontend renders
them as a structured 4-bullet impact analysis.

Taxonomy (added in v0.4):
- INDUSTRIES: single-pick per signal (which Cognizant vertical).
- TECH_TOPICS + NEWS_TOPICS: multi-pick per signal. Stored together in the
  `topics` column. Frontend visually separates them by checking which list
  each value belongs to.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON


class Impact:
    HI = "hi"
    MED = "med"
    LO = "lo"


class SourceType:
    OFFICIAL = "official"
    FINANCIAL = "financial"
    MEDIA = "media"


class SignalStatus:
    """Lifecycle states. Public API only returns PUBLISHED.
    Draft → admin review → Published, or → Discarded (soft delete).
    Discarded → Restore → back to Draft."""
    DRAFT = "draft"
    PUBLISHED = "published"
    DISCARDED = "discarded"  # soft-deleted; can be restored


# ─── TAXONOMY (v0.4) ─────────────────────────────────────────────────────────
# Single source of truth. Both backend (classification) and frontend
# (filter pills, edit form dropdowns) read from these constants via /api/taxonomy.

INDUSTRIES = [
    "BFSI",
    "Healthcare",
    "Manufacturing",
    "Retail & CPG",
    "Insurance",
    "Telecom",
    "Energy & Utilities",
    "Public Sector",
    "Communications & Media",
    "Life Sciences",
    "Travel & Hospitality",
    "Cross-industry",
]

# Technology topics — what tech the signal involves
TECH_TOPICS = [
    "AI & GenAI",
    "Cloud",
    "Data & Analytics",
    "Cybersecurity",
    "Sustainability",
    "Quantum",
    "Edge / IoT",
    "Platform / SaaS",
]

# News-category topics — what kind of news it is
NEWS_TOPICS = [
    "Contract win",
    "Partnership",
    "M&A",
    "Leadership change",
    "Product launch",
    "Earnings",
    "Layoff / restructure",
    "Regulatory",
    "IP / patent",
    "Investment / funding",
    "Strategy / direction",
]

# Combined for validation / lookup. Topics field on Signal can contain any.
ALL_TOPICS = TECH_TOPICS + NEWS_TOPICS

# Legacy → new mapping used by the one-time data migration.
# Old "tags" were event types (Contract, Partnership, etc.) — these become
# news topics. Old "topics" had a mix of industries and tech — split them.
LEGACY_TAG_TO_NEWS_TOPIC = {
    "Contract": "Contract win",
    "Partnership": "Partnership",
    "M&A": "M&A",
    "Leadership": "Leadership change",
    "Product": "Product launch",
}

LEGACY_TOPIC_TO_INDUSTRY = {
    "BFSI": "BFSI",
    "Healthcare": "Healthcare",
    "Manufacturing": "Manufacturing",
    "Retail": "Retail & CPG",
    "Retail & CPG": "Retail & CPG",
    "Insurance": "Insurance",
    "Telecom": "Telecom",
    "Energy": "Energy & Utilities",
    "Energy & Utilities": "Energy & Utilities",
    "Public Sector": "Public Sector",
    "Life Sciences": "Life Sciences",
    "Travel": "Travel & Hospitality",
    "Travel & Hospitality": "Travel & Hospitality",
}

LEGACY_TOPIC_TO_TECH = {
    "AI/GenAI": "AI & GenAI",
    "AI & GenAI": "AI & GenAI",
    "Cloud": "Cloud",
    "Data": "Data & Analytics",
    "Data & Analytics": "Data & Analytics",
    "Cybersecurity": "Cybersecurity",
    "Sustainability": "Sustainability",
}


# ─── THE SIGNAL ──────────────────────────────────────────────────────────────

class Signal(SQLModel, table=True):
    """One news item about a competitor, with a Cognizant impact analysis."""

    id: Optional[int] = Field(default=None, primary_key=True)

    # ─── Draft vs Published vs Discarded ──────────────────────────────────
    # Public API only returns PUBLISHED signals. Drafts are scraper output
    # awaiting analyst review. Discarded are soft-deleted items the admin
    # can restore from the discarded view.
    status: str = Field(default=SignalStatus.PUBLISHED, index=True)

    # ─── Article basics (filled by analyst or scraper) ────────────────────
    competitor: str = Field(index=True)
    headline: str
    description: str
    # URL is unique to prevent accidental duplicates from scraping.
    # When admin clicks "Add as draft anyway" on a known duplicate, the URL
    # gets a #cipulse-dup-<timestamp> suffix to satisfy this constraint while
    # keeping the original source URL recoverable by stripping the suffix.
    url: str = Field(unique=True)

    # ─── Classification (filled by analyst) ──────────────────────────────
    # v0.4: industry is the single-pick vertical (BFSI, Healthcare, ...).
    # topics is the multi-pick list combining tech areas and news categories.
    # Legacy `tags` field retained for backward compat with existing rows but
    # NOT used by new code paths — frontend reads industry + topics only.
    industry: Optional[str] = Field(default=None, index=True)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    topics: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    impact: str = Field(default=Impact.MED)

    # ─── Source provenance ───────────────────────────────────────────────
    source: str
    source_type: str = Field(default=SourceType.OFFICIAL)
    source_priority: int = Field(default=1)

    # ─── Timestamps ──────────────────────────────────────────────────────
    published_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    discarded_at: Optional[datetime] = Field(default=None)  # soft-delete timestamp

    # ─── Impact analysis ─────────────────────────────────────────────────
    impact_pipeline_risk: Optional[str] = Field(default=None)
    impact_verticals: Optional[str] = Field(default=None)
    impact_opportunity: Optional[str] = Field(default=None)
    impact_intel_gap: Optional[str] = Field(default=None)
    analyst_review: Optional[str] = Field(default=None)  # Desktop: single review field
    analyst_name: Optional[str] = Field(default=None)


# ─── SOURCE — scraper configuration, editable from admin UI ──────────────────

class ScraperType:
    """How the scraper should fetch articles from this source."""
    SITEMAP = "sitemap"           # XML sitemap (sitemap.xml or wp-sitemap.xml)
    HTML_LISTING = "html_listing"  # Listing page where article URLs are linked
    RSS = "rss"                   # RSS / Atom feed


class Source(SQLModel, table=True):
    """A configurable scraping target. Replaces the hardcoded SCRAPERS dict.

    P1 sources are official competitor newsrooms (one per competitor).
    P2 sources are third-party media; each P2 row targets a specific
    competitor (e.g. Livemint's accenture tag page targets Accenture).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str  # Human-readable label, e.g. "Accenture" or "Livemint — Accenture"
    url: str   # Sitemap URL or listing URL
    priority: int = Field(default=1, index=True)  # 1 = P1 (official), 2 = P2 (media)
    scraper_type: str = Field(default=ScraperType.SITEMAP)
    # Which competitor's signals this source produces. For P1 official sources
    # this is the competitor's name. For P2 sources it's the competitor that
    # this specific tag-page covers (e.g. "Accenture" for livemint/topic/accenture).
    competitor: str = Field(index=True)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── KNOWHUB MAPPING — auto-suggest relevant internal links per signal ──────

class KnowHubMapping(SQLModel, table=True):
    """Maps (industry, topic) → KnowHub URL for auto-suggested links.

    Two-tier fallback system:
    - Specific: both `industry` and `topic` set → shows for that exact combo
    - Fallback: only `industry` set (topic=NULL) → shows for any signal in that industry
    - Global: both NULL → shows for all signals (rare; use sparingly)

    Uniqueness enforced on (industry, topic) pair via index. Frontend lookup
    checks specific matches first (any topic on the signal matches a rule),
    falls back to industry-only, then shows nothing if no rule matches.
    """

    __tablename__ = "knowhub_mapping"

    id: Optional[int] = Field(default=None, primary_key=True)
    # NULL means "any value" — if both are NULL, this is a global fallback.
    # If only topic is NULL, it's an industry-level fallback.
    industry: Optional[str] = Field(default=None, index=True)
    topic: Optional[str] = Field(default=None, index=True)
    label: str  # Display text shown to user, e.g. "BFSI Sales Playbook"
    url: str    # Full KnowHub URL
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
