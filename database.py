"""
database.py — Database connection and session management

SQLite locally, swap DATABASE_URL for PostgreSQL in production.
"""

import logging
from sqlmodel import SQLModel, create_engine, Session, text, select
from config import settings

log = logging.getLogger(__name__)


engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args=(
        {"check_same_thread": False}
        if settings.database_url.startswith("sqlite")
        else {}
    ),
)


def init_db() -> None:
    """Create all tables defined in models.py if they don't already exist.

    Also runs simple migrations for added columns (since SQLModel.create_all
    only creates new tables, it doesn't ALTER existing ones).
    """
    SQLModel.metadata.create_all(engine)
    _migrate_add_column("status", "VARCHAR DEFAULT 'published' NOT NULL")
    _migrate_add_column("discarded_at", "DATETIME")
    _migrate_add_column("industry", "VARCHAR")
    _migrate_add_column("analyst_review", "TEXT")  # Desktop: single review field
    _seed_default_sources()
    _backfill_signal_taxonomy()


def _migrate_add_column(column_name: str, column_def: str) -> None:
    """Add a column to the signal table if it doesn't already exist."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(signal)"))
            cols = [row[1] for row in result.fetchall()]
            if column_name not in cols:
                conn.execute(text(f"ALTER TABLE signal ADD COLUMN {column_name} {column_def}"))
                conn.commit()
                log.info(f"Migration: added '{column_name}' column to signal table")
            else:
                log.debug(f"Migration: '{column_name}' column already present")
    except Exception as e:
        log.warning(f"Column migration check failed for '{column_name}' (may be normal on first run): {e}")


def _seed_default_sources() -> None:
    """First-run seed: populate the Source table with the original hardcoded
    P1 + P2 sources so existing installs keep scraping the same things.

    No-op if any Source rows already exist (i.e. on every run after the first).
    """
    from models import Source, ScraperType  # avoid circular import at module load

    try:
        with Session(engine) as s:
            count = len(s.exec(select(Source)).all())
            if count > 0:
                log.debug(f"Source table already seeded ({count} rows)")
                return

            # P1 — 12 Competitors (official newsrooms, all enabled)
            p1_seed = [
                ("Accenture",        "https://newsroom.accenture.com",                      ScraperType.SITEMAP, "Accenture"),
                ("TCS",              "https://www.tcs.com/who-we-are/newsroom",              ScraperType.SITEMAP, "TCS"),
                ("Infosys",          "https://www.infosys.com/newsroom.html",                ScraperType.SITEMAP, "Infosys"),
                ("Wipro",            "https://www.wipro.com/newsroom/",                      ScraperType.SITEMAP, "Wipro"),
                ("HCL Technologies", "https://www.hcltech.com/newsroom",                     ScraperType.SITEMAP, "HCL Technologies"),
                ("IBM",              "https://newsroom.ibm.com",                             ScraperType.SITEMAP, "IBM"),
                ("Deloitte",         "https://www2.deloitte.com/us/en/pages/about-deloitte/articles/press-releases.html", ScraperType.SITEMAP, "Deloitte"),
                ("Capgemini",        "https://www.capgemini.com/news/",                      ScraperType.SITEMAP, "Capgemini"),
                ("Tech Mahindra",    "https://www.techmahindra.com/en-in/newsroom/",         ScraperType.SITEMAP, "Tech Mahindra"),
                ("Globant",          "https://www.globant.com/newsroom",                     ScraperType.SITEMAP, "Globant"),
                ("Genpact",          "https://www.genpact.com/newsroom",                     ScraperType.SITEMAP, "Genpact"),
                ("DXC Technology",   "https://dxc.com/us/en/newsroom",                       ScraperType.SITEMAP, "DXC Technology"),
            ]
            for name, url, st, comp in p1_seed:
                s.add(Source(name=name, url=url, priority=1, scraper_type=st, competitor=comp, enabled=True))

            # P2 — 14 Media sources (general tech news, disabled by default for initial testing)
            p2_seed = [
                ("Forbes — Cloud",      "https://www.forbes.com/cloud/"),
                ("Forbes — AI",         "https://www.forbes.com/ai-big-data/"),
                ("CRN — News",          "https://www.crn.com/news/"),
                ("CRN — Slide Shows",   "https://www.crn.com/slide-shows/"),
                ("CNBC — Technology",   "https://www.cnbc.com/technology/"),
                ("Silicon Angle",       "https://siliconangle.com/category/cloud/"),
                ("ZDNet — Cloud",       "https://www.zdnet.com/topic/cloud/"),
                ("CNN — Tech",          "https://edition.cnn.com/business/tech"),
                ("Reuters — Technology", "https://www.reuters.com/technology/"),
                ("NYTimes — Technology", "https://www.nytimes.com/section/technology"),
                ("Data Centre Dynamics", "https://www.datacenterdynamics.com/en/"),
                ("W.Media",             "https://w.media/"),
                ("Silverlinings",       "https://www.silverliningsinfo.com/"),
                ("erp.today",           "https://erp.today/category/erp-news/"),
            ]
            for name, url in p2_seed:
                s.add(Source(name=name, url=url, priority=2, scraper_type=ScraperType.HTML_LISTING, competitor="Technology", enabled=False))

            s.commit()
            log.info("Seeded Source table with 12 P1 (enabled) + 14 P2 (disabled) = 26 total")
    except Exception as e:
        log.warning(f"Source seeding failed (may be normal on first run before tables exist): {e}")


def _backfill_signal_taxonomy() -> None:
    """One-time backfill: for any signal that has no `industry` set, derive it
    from old `topics`, and re-classify old `tags` into the new `topics` field.

    Runs every startup but only writes signals that need updating, so it's
    idempotent and cheap after the first run.
    """
    from models import Signal, LEGACY_TOPIC_TO_INDUSTRY, LEGACY_TOPIC_TO_TECH, LEGACY_TAG_TO_NEWS_TOPIC, ALL_TOPICS, INDUSTRIES

    try:
        with Session(engine) as s:
            sigs = s.exec(select(Signal)).all()
            updated = 0
            for sig in sigs:
                changed = False

                # Industry: derive from old topics if not set yet
                if not sig.industry:
                    derived = None
                    for old_topic in (sig.topics or []):
                        if old_topic in LEGACY_TOPIC_TO_INDUSTRY:
                            derived = LEGACY_TOPIC_TO_INDUSTRY[old_topic]
                            break
                    if not derived:
                        derived = "Cross-industry"
                    sig.industry = derived
                    changed = True

                # Topics: re-classify into new taxonomy if any value looks legacy
                new_topics = []
                seen = set()
                for t in (sig.topics or []):
                    # Convert legacy tech topic if needed
                    mapped = LEGACY_TOPIC_TO_TECH.get(t, t)
                    # Drop legacy industry values from topics (they live in `industry` now)
                    if mapped in LEGACY_TOPIC_TO_INDUSTRY:
                        continue
                    if mapped in ALL_TOPICS and mapped not in seen:
                        new_topics.append(mapped); seen.add(mapped)
                # Add news topics derived from old tags
                for tag in (sig.tags or []):
                    mapped = LEGACY_TAG_TO_NEWS_TOPIC.get(tag, tag)
                    if mapped in ALL_TOPICS and mapped not in seen:
                        new_topics.append(mapped); seen.add(mapped)

                if new_topics != (sig.topics or []):
                    sig.topics = new_topics
                    changed = True

                if changed:
                    s.add(sig)
                    updated += 1

            if updated:
                s.commit()
                log.info(f"Taxonomy backfill: updated {updated} signal(s) to new industry/topics scheme")
            else:
                log.debug("Taxonomy backfill: no signals needed updating")
    except Exception as e:
        log.warning(f"Taxonomy backfill failed (may be normal on fresh DB): {e}")


# Backwards-compat alias so older code paths still work
def _migrate_add_status_column():
    _migrate_add_column("status", "VARCHAR DEFAULT 'published' NOT NULL")


def get_session() -> Session:
    """Hand out a database session. Use as `with get_session() as s: ...`"""
    return Session(engine)
