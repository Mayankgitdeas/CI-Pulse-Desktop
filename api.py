"""
api.py — REST API endpoints

Namespaces:
  /api/signals          — PUBLIC, read-only (mobile app + web frontend hit these)
                          Only returns published signals; drafts are hidden.
  /api/admin/signals    — ADMIN, requires login (admin panel hits these)
                          Manages published signals (create, edit, delete).
  /api/admin/drafts     — ADMIN, requires login
                          Manages draft signals from scraper (publish, edit, discard).
  /api/admin/scrape     — ADMIN, requires login
                          Triggers scraping of all 5 competitor newsrooms.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import select

from database import get_session
from models import Signal, SignalStatus, Source, ScraperType, KnowHubMapping, INDUSTRIES, TECH_TOPICS, NEWS_TOPICS, ALL_TOPICS
from auth import require_admin
from scraper_playwright import scrape_all_playwright

log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API — only PUBLISHED signals visible
# ═════════════════════════════════════════════════════════════════════════════

public_router = APIRouter(prefix="/api", tags=["public"])


@public_router.get("/signals")
def list_signals(
    days: int = Query(30, ge=1, le=365),
    competitor: Optional[str] = Query(None),
    industry: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    impact: Optional[str] = Query(None),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as s:
        query = (
            select(Signal)
            .where(Signal.status == SignalStatus.PUBLISHED)
            .where(Signal.published_at >= cutoff)
        )
        if competitor:
            query = query.where(Signal.competitor == competitor)
        if industry:
            query = query.where(Signal.industry == industry)
        if impact:
            query = query.where(Signal.impact == impact)
        query = query.order_by(Signal.published_at.desc())
        results = s.exec(query).all()
    if topic:
        results = [r for r in results if topic in r.topics]
    if tag:
        results = [r for r in results if tag in r.tags]
    return results


@public_router.get("/signals/{signal_id}")
def get_signal(signal_id: int):
    with get_session() as s:
        signal = s.exec(
            select(Signal)
            .where(Signal.id == signal_id)
            .where(Signal.status == SignalStatus.PUBLISHED)
        ).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        return signal


@public_router.get("/health")
def health():
    with get_session() as s:
        pub = len(s.exec(select(Signal).where(Signal.status == SignalStatus.PUBLISHED)).all())
        drf = len(s.exec(select(Signal).where(Signal.status == SignalStatus.DRAFT)).all())
    return {
        "status": "healthy",
        "service": "competitor-pulse",
        "version": "0.3.0-scraper",
        "published_count": pub,
        "draft_count": drf,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ADMIN API — signals CRUD
# ═════════════════════════════════════════════════════════════════════════════

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


class SignalIn(BaseModel):
    competitor: str
    headline: str
    description: str
    url: str
    industry: Optional[str] = None  # v0.4: single-pick vertical
    tags: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    impact: str = "med"
    source: str
    source_type: str = "official"
    source_priority: int = 1
    published_at: datetime
    impact_pipeline_risk: Optional[str] = None
    impact_verticals: Optional[str] = None
    impact_opportunity: Optional[str] = None
    impact_intel_gap: Optional[str] = None
    analyst_review: Optional[str] = None
    analyst_name: Optional[str] = None
    status: str = SignalStatus.PUBLISHED


@admin_router.post("/signals", status_code=201)
def create_signal(payload: SignalIn, admin: str = Depends(require_admin)):
    signal = Signal(
        status=payload.status,
        competitor=payload.competitor,
        headline=payload.headline,
        description=payload.description,
        url=payload.url,
        industry=payload.industry,
        tags=payload.tags,
        topics=payload.topics,
        impact=payload.impact,
        source=payload.source,
        source_type=payload.source_type,
        source_priority=payload.source_priority,
        published_at=payload.published_at,
        impact_pipeline_risk=payload.impact_pipeline_risk,
        impact_verticals=payload.impact_verticals,
        impact_opportunity=payload.impact_opportunity,
        impact_intel_gap=payload.impact_intel_gap,
        analyst_review=payload.analyst_review,
        analyst_name=payload.analyst_name or admin,
    )
    with get_session() as s:
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} created {signal.status} signal {signal.id}: {signal.headline[:60]}")
    return signal


@admin_router.put("/signals/{signal_id}")
def update_signal(signal_id: int, payload: SignalIn, admin: str = Depends(require_admin)):
    with get_session() as s:
        signal = s.exec(select(Signal).where(Signal.id == signal_id)).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        for field, value in payload.model_dump().items():
            setattr(signal, field, value)
        signal.updated_at = datetime.now(timezone.utc)
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} updated signal {signal_id}")
    return signal


@admin_router.delete("/signals/{signal_id}")
def delete_signal(
    signal_id: int,
    hard: bool = Query(False, description="If true, permanently delete; otherwise soft-delete (move to Discarded)"),
    admin: str = Depends(require_admin),
):
    """Soft-delete by default (status='discarded'). Pass ?hard=true to permanently remove."""
    with get_session() as s:
        signal = s.exec(select(Signal).where(Signal.id == signal_id)).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        if hard:
            s.delete(signal)
            s.commit()
            log.info(f"Admin {admin} HARD-deleted signal {signal_id}")
            return {"hard_deleted": signal_id}
        signal.status = SignalStatus.DISCARDED
        signal.discarded_at = datetime.now(timezone.utc)
        signal.updated_at = datetime.now(timezone.utc)
        s.add(signal)
        s.commit()
    log.info(f"Admin {admin} discarded signal {signal_id}")
    return {"discarded": signal_id}


@admin_router.post("/signals/{signal_id}/restore")
def restore_signal(signal_id: int, admin: str = Depends(require_admin)):
    """Restore a discarded signal back to draft state for analyst review."""
    with get_session() as s:
        signal = s.exec(
            select(Signal)
            .where(Signal.id == signal_id)
            .where(Signal.status == SignalStatus.DISCARDED)
        ).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Discarded signal not found")
        signal.status = SignalStatus.DRAFT
        signal.discarded_at = None
        signal.updated_at = datetime.now(timezone.utc)
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} restored signal {signal_id}")
    return signal


@admin_router.get("/discarded")
def list_discarded(admin: str = Depends(require_admin)):
    """List soft-deleted signals (most recently discarded first)."""
    with get_session() as s:
        return s.exec(
            select(Signal)
            .where(Signal.status == SignalStatus.DISCARDED)
            .order_by(Signal.discarded_at.desc())
        ).all()


@admin_router.get("/signals")
def list_all_signals(admin: str = Depends(require_admin)):
    """List PUBLISHED signals only — drafts visible via /drafts."""
    with get_session() as s:
        return s.exec(
            select(Signal)
            .where(Signal.status == SignalStatus.PUBLISHED)
            .order_by(Signal.published_at.desc())
        ).all()


# ═════════════════════════════════════════════════════════════════════════════
# DRAFTS — pre-filled by scraper, await analyst review
# ═════════════════════════════════════════════════════════════════════════════

@admin_router.get("/drafts")
def list_drafts(admin: str = Depends(require_admin)):
    with get_session() as s:
        return s.exec(
            select(Signal)
            .where(Signal.status == SignalStatus.DRAFT)
            .order_by(Signal.published_at.desc())
        ).all()


@admin_router.post("/drafts/{signal_id}/publish")
def publish_draft(signal_id: int, payload: SignalIn, admin: str = Depends(require_admin)):
    """Convert a draft to published with analyst's added impact analysis."""
    with get_session() as s:
        signal = s.exec(
            select(Signal).where(Signal.id == signal_id).where(Signal.status == SignalStatus.DRAFT)
        ).first()
        if not signal:
            raise HTTPException(status_code=404, detail="Draft not found")
        for field, value in payload.model_dump().items():
            setattr(signal, field, value)
        signal.status = SignalStatus.PUBLISHED
        signal.updated_at = datetime.now(timezone.utc)
        if not signal.analyst_name:
            signal.analyst_name = admin
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} published draft {signal_id}: {signal.headline[:60]}")
    return signal


# ═════════════════════════════════════════════════════════════════════════════
# SCRAPER — fetches new articles from 5 P1 newsrooms + P2 news portals
# ═════════════════════════════════════════════════════════════════════════════

class DuplicateAdd(BaseModel):
    """Force-add a known duplicate scraped article as a new draft."""
    competitor: str
    headline: str
    description: str = ""
    url: str
    published_at: str        # ISO date
    source: str
    source_priority: int = 1
    suggested_tag: Optional[str] = None
    suggested_topics: list[str] = Field(default_factory=list)
    suggested_impact: str = "med"


@admin_router.post("/drafts/add_duplicate")
def add_duplicate_as_draft(payload: DuplicateAdd, admin: str = Depends(require_admin)):
    """Create a new draft from a scraped article that was flagged as duplicate.

    The URL gets a #cipulse-dup-<timestamp> suffix appended to satisfy the
    unique constraint while keeping the original source URL recoverable.
    """
    import time as _time
    suffix = f"#cipulse-dup-{int(_time.time() * 1000)}"
    unique_url = payload.url.split("#")[0] + suffix  # strip any existing fragment then add ours

    try:
        published_at = datetime.fromisoformat(payload.published_at.replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid published_at format")

    signal = Signal(
        status=SignalStatus.DRAFT,
        competitor=payload.competitor,
        headline=payload.headline,
        description=payload.description or payload.headline,
        url=unique_url,
        tags=[payload.suggested_tag] if payload.suggested_tag else [],
        topics=payload.suggested_topics,
        impact=payload.suggested_impact,
        source=payload.source,
        source_type="official" if payload.source_priority == 1 else "media",
        source_priority=payload.source_priority,
        published_at=published_at,
    )
    with get_session() as s:
        s.add(signal)
        s.commit()
        s.refresh(signal)
    log.info(f"Admin {admin} force-added duplicate {signal.id}: {signal.headline[:60]}")
    return signal


@admin_router.post("/scrape")
async def trigger_scrape(
    days: int = Query(14, ge=1, le=180),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    admin: str = Depends(require_admin),
):
    """Scrape all newsrooms (P1) and news portals (P2); create drafts.

    Duplicate detection looks at ACTIVE signals (drafts + published).
    Discarded signals don't count as duplicates — re-scraping can resurface them.
    """
    log.info(f"Admin {admin} triggered scrape (days={days}, from={from_date}, to={to_date})")

    parsed_from = None
    parsed_to = None
    if from_date:
        try:
            parsed_from = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD.")
    if to_date:
        try:
            parsed_to = datetime.fromisoformat(to_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD.")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date.")

    # Hardening: catch *anything* from scraper or DB and report a useful error
    # so the admin UI gets a real message instead of an opaque 500.
    try:
        # Load enabled sources
        with get_session() as s:
            sources = s.exec(select(Source).where(Source.enabled == True)).all()
        
        cutoff = parsed_from if parsed_from else (datetime.now(timezone.utc) - timedelta(days=days))
        result = await scrape_all_playwright(sources, cutoff)
    except Exception as e:
        import traceback
        log.error(f"scrape_all crashed: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Scraper crashed: {type(e).__name__}: {str(e)[:300]}"
        )

    new_drafts = 0
    duplicates = []
    errors = 0
    seen_in_batch = set()  # URLs added in THIS scrape run (prevents in-batch duplicate INSERT)

    try:
      with get_session() as s:
        for article in result.articles:
            # Skip if we already added this URL earlier in THIS batch
            if article.url in seen_in_batch:
                duplicates.append({
                    "scraped_headline": article.headline,
                    "scraped_url": article.url,
                    "scraped_competitor": article.competitor,
                    "scraped_published_at": article.published_at.isoformat(),
                    "scraped_source": article.source,
                    "scraped_source_priority": article.source_priority,
                    "scraped_suggested_tag": article.suggested_tag,
                    "scraped_suggested_topics": article.suggested_topics or [],
                    "scraped_suggested_impact": article.suggested_impact,
                    "scraped_description": article.description,
                    "existing_id": None,
                    "existing_status": "in_batch",
                    "existing_headline": article.headline,
                })
                continue
            # Look for active (non-discarded) matching URL in DB
            existing = s.exec(
                select(Signal)
                .where(Signal.url == article.url)
                .where(Signal.status != SignalStatus.DISCARDED)
            ).first()
            if existing:
                duplicates.append({
                    "scraped_headline": article.headline,
                    "scraped_url": article.url,
                    "scraped_competitor": article.competitor,
                    "scraped_published_at": article.published_at.isoformat(),
                    "scraped_source": article.source,
                    "scraped_source_priority": article.source_priority,
                    "scraped_suggested_tag": article.suggested_tag,
                    "scraped_suggested_topics": article.suggested_topics or [],
                    "scraped_suggested_impact": article.suggested_impact,
                    "scraped_description": article.description,
                    "existing_id": existing.id,
                    "existing_status": existing.status,
                    "existing_headline": existing.headline,
                })
                continue
            try:
                signal = Signal(
                    status=SignalStatus.DRAFT,
                    competitor=article.competitor,
                    headline=article.headline,
                    description=article.description,
                    url=article.url,
                    industry=article.suggested_industry,
                    tags=[article.suggested_tag] if article.suggested_tag else [],
                    topics=article.suggested_topics or [],
                    impact=article.suggested_impact,
                    source=article.source,
                    source_type="official" if article.source_priority == 1 else "media",
                    source_priority=article.source_priority,
                    published_at=article.published_at,
                )
                s.add(signal)
                s.flush()  # flush now to catch IntegrityError per-article, not at batch commit
                seen_in_batch.add(article.url)
                new_drafts += 1
            except Exception as e:
                s.rollback()
                log.warning(f"Could not save article {article.url}: {e}")
                errors += 1
        s.commit()
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error(f"DB write during scrape crashed: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"DB error during scrape: {type(e).__name__}: {str(e)[:300]}"
        )

    log.info(
        f"Scrape: {new_drafts} new drafts, {len(duplicates)} duplicates, "
        f"{errors} errors, {len(result.failed_sources)} sources failed"
    )

    return {
        "new_drafts": new_drafts,
        "duplicates_skipped": len(duplicates),
        "duplicates": duplicates,
        "errors": errors,
        "failed_sources": result.failed_sources,
        "duration_seconds": round(result.duration_seconds, 1),
        "total_articles_found": len(result.articles),
        "date_range_used": {
            "from": (parsed_from or (datetime.now(timezone.utc) - timedelta(days=days))).isoformat(),
            "to": (parsed_to or datetime.now(timezone.utc)).isoformat(),
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# TAXONOMY — single source of truth for industries + topics
# ═════════════════════════════════════════════════════════════════════════════

@public_router.get("/taxonomy")
def get_taxonomy():
    """Returns industry list + topic groups (tech and news).
    Used by admin form dropdowns and frontend filter pills."""
    return {
        "industries": INDUSTRIES,
        "tech_topics": TECH_TOPICS,
        "news_topics": NEWS_TOPICS,
        "all_topics": ALL_TOPICS,
    }


# ═════════════════════════════════════════════════════════════════════════════
# ADMIN API — Source CRUD (replaces hardcoded SCRAPERS dict)
# ═════════════════════════════════════════════════════════════════════════════

class SourceIn(BaseModel):
    """Payload for create/update of a Source row."""
    name: str
    url: str
    priority: int = 1  # 1 = P1, 2 = P2
    scraper_type: str = ScraperType.SITEMAP
    competitor: str
    enabled: bool = True


@admin_router.get("/sources")
def list_sources(
    priority: Optional[int] = Query(None, ge=1, le=2),
    admin: str = Depends(require_admin),
):
    """List sources, optionally filtered by priority (1 or 2)."""
    with get_session() as s:
        query = select(Source)
        if priority is not None:
            query = query.where(Source.priority == priority)
        query = query.order_by(Source.priority, Source.name)
        return s.exec(query).all()


@admin_router.post("/sources", status_code=201)
def create_source(payload: SourceIn, admin: str = Depends(require_admin)):
    if payload.priority not in (1, 2):
        raise HTTPException(status_code=400, detail="priority must be 1 or 2")
    if payload.scraper_type not in (ScraperType.SITEMAP, ScraperType.HTML_LISTING, ScraperType.RSS):
        raise HTTPException(status_code=400, detail=f"scraper_type must be sitemap, html_listing, or rss")
    source = Source(
        name=payload.name.strip(),
        url=payload.url.strip(),
        priority=payload.priority,
        scraper_type=payload.scraper_type,
        competitor=payload.competitor.strip(),
        enabled=payload.enabled,
    )
    with get_session() as s:
        s.add(source)
        s.commit()
        s.refresh(source)
    log.info(f"Admin {admin} created source #{source.id}: {source.name} ({source.url})")
    return source


@admin_router.put("/sources/{source_id}")
def update_source(source_id: int, payload: SourceIn, admin: str = Depends(require_admin)):
    with get_session() as s:
        source = s.exec(select(Source).where(Source.id == source_id)).first()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        source.name = payload.name.strip()
        source.url = payload.url.strip()
        source.priority = payload.priority
        source.scraper_type = payload.scraper_type
        source.competitor = payload.competitor.strip()
        source.enabled = payload.enabled
        s.add(source)
        s.commit()
        s.refresh(source)
    log.info(f"Admin {admin} updated source #{source_id}")
    return source


@admin_router.patch("/sources/{source_id}/toggle")
def toggle_source(source_id: int, admin: str = Depends(require_admin)):
    """Flip enabled flag. Used by the toggle switch on each source row."""
    with get_session() as s:
        source = s.exec(select(Source).where(Source.id == source_id)).first()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        source.enabled = not source.enabled
        s.add(source)
        s.commit()
        s.refresh(source)
    log.info(f"Admin {admin} toggled source #{source_id} -> enabled={source.enabled}")
    return source


@admin_router.delete("/sources/{source_id}")
def delete_source(source_id: int, admin: str = Depends(require_admin)):
    with get_session() as s:
        source = s.exec(select(Source).where(Source.id == source_id)).first()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        s.delete(source)
        s.commit()
    log.info(f"Admin {admin} deleted source #{source_id}")
    return {"deleted": source_id}


# ═════════════════════════════════════════════════════════════════════════════
# KNOWHUB MAPPING — auto-suggest internal Cognizant KnowHub links per signal
# ═════════════════════════════════════════════════════════════════════════════

class KnowHubMappingIn(BaseModel):
    """Payload for create/update of a KnowHub mapping."""
    industry: Optional[str] = None  # NULL = "any industry"
    topic: Optional[str] = None     # NULL = industry-level fallback (no topic specified)
    label: str
    url: str


@admin_router.get("/knowhub")
def list_knowhub_mappings(admin: str = Depends(require_admin)):
    """List all KnowHub URL mappings."""
    with get_session() as s:
        return s.exec(
            select(KnowHubMapping).order_by(
                KnowHubMapping.industry, KnowHubMapping.topic
            )
        ).all()


@admin_router.post("/knowhub", status_code=201)
def create_knowhub_mapping(payload: KnowHubMappingIn, admin: str = Depends(require_admin)):
    if not payload.label.strip() or not payload.url.strip():
        raise HTTPException(status_code=400, detail="Label and URL are required")
    
    # Check for duplicate (industry, topic) pair
    with get_session() as s:
        existing = s.exec(
            select(KnowHubMapping)
            .where(KnowHubMapping.industry == payload.industry)
            .where(KnowHubMapping.topic == payload.topic)
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"A mapping for ({payload.industry or 'Any'}, {payload.topic or 'Any topic'}) already exists"
            )
        
        mapping = KnowHubMapping(
            industry=payload.industry,
            topic=payload.topic,
            label=payload.label.strip(),
            url=payload.url.strip(),
        )
        s.add(mapping)
        s.commit()
        s.refresh(mapping)
    log.info(f"Admin {admin} created KnowHub mapping #{mapping.id}: ({mapping.industry}, {mapping.topic}) → {mapping.url}")
    return mapping


@admin_router.put("/knowhub/{mapping_id}")
def update_knowhub_mapping(
    mapping_id: int, payload: KnowHubMappingIn, admin: str = Depends(require_admin)
):
    with get_session() as s:
        mapping = s.exec(select(KnowHubMapping).where(KnowHubMapping.id == mapping_id)).first()
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping not found")
        
        # Check for duplicate if industry/topic changed
        if (mapping.industry, mapping.topic) != (payload.industry, payload.topic):
            dup = s.exec(
                select(KnowHubMapping)
                .where(KnowHubMapping.industry == payload.industry)
                .where(KnowHubMapping.topic == payload.topic)
            ).first()
            if dup:
                raise HTTPException(
                    status_code=400,
                    detail=f"A mapping for ({payload.industry or 'Any'}, {payload.topic or 'Any'}) already exists"
                )
        
        mapping.industry = payload.industry
        mapping.topic = payload.topic
        mapping.label = payload.label.strip()
        mapping.url = payload.url.strip()
        s.add(mapping)
        s.commit()
        s.refresh(mapping)
    log.info(f"Admin {admin} updated KnowHub mapping #{mapping_id}")
    return mapping


@admin_router.delete("/knowhub/{mapping_id}")
def delete_knowhub_mapping(mapping_id: int, admin: str = Depends(require_admin)):
    with get_session() as s:
        mapping = s.exec(select(KnowHubMapping).where(KnowHubMapping.id == mapping_id)).first()
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping not found")
        s.delete(mapping)
        s.commit()
    log.info(f"Admin {admin} deleted KnowHub mapping #{mapping_id}")
    return {"deleted": mapping_id}


@public_router.get("/knowhub/lookup")
def lookup_knowhub(
    industry: str = Query(...),
    topics: str = Query(...),  # Comma-separated list
):
    """Find the best-matching KnowHub link for a given signal.
    
    Priority: (industry, topic) specific match → industry-only fallback → None.
    If signal has multiple topics, checks each topic for a specific match.
    First match wins.
    """
    topic_list = [t.strip() for t in topics.split(',') if t.strip()]
    
    with get_session() as s:
        # Try (industry, topic) pairs first — iterate topics in order
        for topic in topic_list:
            match = s.exec(
                select(KnowHubMapping)
                .where(KnowHubMapping.industry == industry)
                .where(KnowHubMapping.topic == topic)
            ).first()
            if match:
                return match
        
        # Fallback to industry-only (topic=NULL)
        match = s.exec(
            select(KnowHubMapping)
            .where(KnowHubMapping.industry == industry)
            .where(KnowHubMapping.topic == None)
        ).first()
        if match:
            return match
        
        # No match
        return None


@public_router.post("/export-pdf")
def export_pdf(payload: dict):
    from pdf_generator import generate_signals_pdf
    from fastapi.responses import Response
    ids = payload.get("signal_ids", [])
    with get_session() as s:
        sigs = s.exec(select(Signal).where(Signal.id.in_(ids))).all()
    dicts = [{
        "competitor": sig.competitor,
        "headline": sig.headline,
        "description": sig.description,
        "industry": sig.industry,
        "topics": sig.topics,
        "impact": sig.impact,
        "analyst_review": sig.analyst_review,
        "source": sig.source,
        "published_at": sig.published_at.isoformat() if sig.published_at else "",
    } for sig in sigs]
    pdf = generate_signals_pdf(dicts, payload.get("title", "Cognizant CI Pulse"))
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="ci-pulse.pdf"'})
