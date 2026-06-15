"""
seed.py — Load sample signals on first run

These have FULL impact analyses already filled in so the admin panel
preview and the mobile app demonstrate the complete experience immediately.
"""

import logging
from datetime import datetime
from sqlmodel import select

from database import get_session
from models import Signal, Impact, SourceType

log = logging.getLogger(__name__)


SEED = [
    {
        "competitor": "Wipro",
        "headline": "Wipro secures 8-year, $1B+ strategic transformation deal with Olam Group",
        "description": "Committed spend of $800M covering farm-to-fork AI capabilities across Olam's agri-business. Wipro's largest announced deal of FY26.",
        "tags": ["Contract"], "topics": ["AI & Gen AI", "Manufacturing"], "impact": Impact.HI,
        "source": "Wipro Newsroom", "source_type": SourceType.OFFICIAL, "source_priority": 1,
        "url": "https://wipro.com/newsroom/wipro-secures-olam-group-strategic-deal-2026",
        "published_at": "2026-04-22T14:00:00+00:00",
        "impact_pipeline_risk": "Direct competitive threat — Olam was on Cognizant's Manufacturing pipeline shortlist as a Q3 prospect. Deal is now closed for 8 years.",
        "impact_verticals": "Manufacturing & CPG most exposed. Adjacent risk to BFSI clients where Wipro's AI scale story will now be referenced.",
        "impact_opportunity": "Counter-position our consumer-goods AI playbook to similar agri-business prospects (ITC, Britannia, Marico). Move fast — Wipro will lead with this case study for 12 months.",
        "impact_intel_gap": "Need internal read on whether Cognizant Sales had active conversations with Olam and what specifically lost us the deal. Pricing? Capability? Relationship?",
        "analyst_name": "admin",
    },
    {
        "competitor": "TCS",
        "headline": "TCS and Siemens Energy forge strategic AI partnership for intelligent operations",
        "description": "Multi-year agreement targets AI-led transformation including predictive maintenance and cloud migration at industrial scale.",
        "tags": ["Partnership"], "topics": ["AI & Gen AI", "Manufacturing"], "impact": Impact.HI,
        "source": "TCS Newsroom", "source_type": SourceType.OFFICIAL, "source_priority": 1,
        "url": "https://www.tcs.com/newsroom/press-release/tcs-siemens-energy-strategic-ai-partnership",
        "published_at": "2026-04-27T10:00:00+00:00",
        "impact_pipeline_risk": "TCS now has flagship industrial-AI reference. Threatens our pursuit of similar deals at Schneider, ABB, Honeywell.",
        "impact_verticals": "Manufacturing — especially energy & utilities sub-segment. Predictive maintenance is a key Neuro AI use case for us.",
        "impact_opportunity": "Accelerate publishing of Cognizant Neuro Industrial case studies. Brief Sales on the differentiated story: deeper domain consulting vs TCS's scale.",
        "impact_intel_gap": "What's the multi-year TCV? Is this exclusive to Siemens Energy or extendable to broader Siemens AG?",
        "analyst_name": "admin",
    },
    {
        "competitor": "Infosys",
        "headline": "Infosys Q4 FY26: Revenue ₹46,402 Cr, wins $1.6B UK NHS mega deal",
        "description": "CEO Salil Parekh confirms $3.1B large deal TCV plus a $1.6B NHS engagement announced post-quarter.",
        "tags": ["Leadership"], "topics": ["Healthcare", "Cloud"], "impact": Impact.HI,
        "source": "Infosys Newsroom", "source_type": SourceType.OFFICIAL, "source_priority": 1,
        "url": "https://infosys.com/investors/reports-filings/press-release/q4-fy26-results.html",
        "published_at": "2026-04-23T15:30:00+00:00",
        "impact_pipeline_risk": "$1.6B NHS deal — Cognizant Trizetto's UK/EU Healthcare expansion plays directly against this. Largest single threat in our healthcare pipeline this year.",
        "impact_verticals": "Healthcare — Trizetto, US payers nervous about cross-Atlantic precedent. Public sector globally watching.",
        "impact_opportunity": "Accelerate Trizetto US public-sector pursuits before this becomes the case study Infosys takes worldwide. Brief Healthcare leadership immediately.",
        "impact_intel_gap": "Implementation timeline? Which Infosys subsidiary holds the contract? How does this affect MoD / other UK gov work we pursue?",
        "analyst_name": "admin",
    },
]


def seed_if_empty() -> None:
    """Insert sample signals only if database is empty."""

    with get_session() as s:
        if s.exec(select(Signal).limit(1)).first():
            log.info("Database already populated — skipping seed")
            return

        log.info(f"Seeding {len(SEED)} sample signals...")
        for data in SEED:
            data = data.copy()
            data["published_at"] = datetime.fromisoformat(data["published_at"])
            s.add(Signal(**data))
        s.commit()
        log.info("Seed complete")
