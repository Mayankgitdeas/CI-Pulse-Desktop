# Cognizant Competitor Pulse — Admin Panel Edition

A simplified, human-curated competitive intelligence platform for Cognizant
leadership. No scraping, no AI API costs, no external dependencies — just an
analyst (you) entering signals and impact analyses via a private admin panel,
which then appear instantly in the mobile / web app for all Cognizant users.

---

## What changed from the previous edition

The earlier version had four automated layers: cron scraper, event bus, AI
analyzer (Claude), and REST API. That's the right architecture for full
production but it required ongoing Anthropic API spend, RSS scraping
maintenance, and developer resources to bring up.

**This edition is the pilot-ready simplification:**

| Removed                          | Replaced by                                |
| -------------------------------- | ------------------------------------------ |
| Cron scraper (`scraper.py`)      | Analyst enters signals via admin panel     |
| AI analyzer (`ai_analyzer.py`)   | Analyst writes impact analysis via template|
| Event bus (`event_bus.py`)       | Direct database writes                     |
| Anthropic API integration        | Removed entirely — zero AI cost            |
| APScheduler / background jobs    | Removed — no scheduled work needed         |

**What stays the same:**

The mobile app, the brand system, the architecture pattern, the database
schema, the source priority logic (P1/P2/P3), the bookmark + search +
share features. Public users see no difference — they still see polished
signals with structured impact analyses. They just don't know a human
typed them instead of an AI.

---

## How it works

```
┌───────────────────────────┐         ┌──────────────────────────┐
│   Admin (you / analyst)    │         │  All Cognizant users     │
│   logs into /admin         │         │  open /                  │
│                            │         │                          │
│   1. Enter signal details  │         │  See signal in feed      │
│   2. Fill 4-question       │  ──▶    │  Tap to see structured   │
│      impact template       │ writes  │  4-bullet analysis       │
│   3. Click Publish         │ to DB   │                          │
└───────────────────────────┘         └──────────────────────────┘
            │                                    ▲
            │                                    │
            └──────────► SQLite database ────────┘
                         (a file on disk)
```

That's the whole architecture. One Python file (`main.py`) starts a web
server. Two HTML files serve as the admin panel and the mobile app.
One database file holds all the signals. No external dependencies, no
internet calls during normal operation.

---

## Project files

```
cognizant-pulse-admin/
├── README.md            ← you are here
├── SETUP_GUIDE.md       ← step-by-step setup for non-developers
├── requirements.txt
├── .env.example         ← config template (rename to .env)
│
├── main.py              ← entry point, login flow, route mounting
├── config.py            ← settings from environment
├── models.py            ← Signal data model
├── database.py          ← SQLite/Postgres connection
├── auth.py              ← admin session management
├── api.py               ← REST endpoints (public + admin)
├── seed.py              ← 3 sample signals loaded on first run
│
└── frontend/
    ├── index.html       ← mobile app (public view)
    ├── admin.html       ← admin panel (requires login)
    └── assets/          ← logos and images
```

---

## How to run

```bash
pip install -r requirements.txt
cp .env.example .env
#   edit .env: change ADMIN_PASSWORD and SESSION_SECRET
python main.py
```

Then in your browser:

- **http://localhost:8000/** — mobile app, public view
- **http://localhost:8000/admin** — admin panel (will redirect to login)
- **http://localhost:8000/admin/login** — login screen (use credentials from .env)
- **http://localhost:8000/docs** — interactive API explorer

Default credentials: `admin` / `changeme` (change these in `.env` before sharing the URL with anyone).

---

## The analyst workflow

1. Each morning, open the admin panel and log in
2. Skim competitor newsrooms (Accenture, TCS, Infosys, Wipro, Capgemini)
3. Pick 2–3 signals worth publishing today
4. For each one, click into the form and fill out:
   - Article basics — competitor, headline, summary, source URL, date
   - Classification — type (Contract/Partnership/...), topics, impact level
   - 4-question impact template:
     - Pipeline / client risk
     - Verticals most affected
     - Immediate opportunity
     - Intelligence gap
5. Click "Publish signal" — it's live for all Cognizant users instantly

Realistic time per signal: 5–10 minutes once you're practiced.
Realistic total daily commitment: 30–45 minutes.

---

## What the public sees

The mobile app at `http://localhost:8000/` shows:

- Branded feed with all 5 competitor logos on tabs
- Signal cards with date, headline, summary, source link
- Source priority badges (P1 Official / P2 Wire / P3 Media)
- Bookmark and share buttons on every card
- Search bar across all signals
- Detail view with the structured 4-bullet impact analysis
- "By [analyst name]" attribution under each analysis

No "Claude AI" badges, no "Generate playbook" button. The analysis is human, attributed, and trusted.

---

## Production deployment

When you're ready to take this beyond your laptop:

1. **Database** — replace `DATABASE_URL=sqlite:///./pulse.db` with the
   Azure PostgreSQL Flexible Server connection string.

2. **Authentication** — replace the simple username/password in `auth.py`
   with Azure AD / Entra ID OAuth so only Cognizant employees can log in.

3. **HTTPS** — set `secure=True` on the session cookie in `main.py` and
   ensure the app sits behind Azure Front Door with TLS.

4. **Multiple admins** — replace the single ADMIN_USERNAME / ADMIN_PASSWORD
   with a `users` table containing the list of authorized analysts.

5. **Audit log** — every create/update/delete is currently logged to console;
   in production, persist these to an `audit_log` table for compliance.

Estimated production-readiness effort: **3–4 weeks** for one developer
(versus 12 weeks for the full automated version).

---

## Cost comparison

| Item                    | Original (automated) | This edition (manual) |
| ----------------------- | -------------------- | --------------------- |
| Build cost              | ₹60–80L              | ₹15–20L               |
| Build timeline          | 12 weeks             | 3–4 weeks             |
| Azure hosting           | ₹2–4L / month        | ₹0.5–1L / month       |
| Anthropic API           | ₹0.5–1L / month      | ₹0                    |
| Scraping service        | $200–400 / month     | ₹0                    |
| Analyst time            | None                 | 30–45 min / day       |
| **Total monthly run**   | **₹2.5–5L**          | **₹0.5–1L**           |

The analyst-curated edition is roughly 70–80% cheaper to run and ~3x
faster to launch — at the cost of one person's morning routine.

---

## Why this is the right starting point

Most successful internal tools at large companies launch with humans in
the loop. Once the product is loved by leadership, automation is added
selectively to the parts that benefit from it most. Starting fully
automated risks building the wrong workflows that no one ever wanted.

The full automated version remains the long-term plan — it's documented
in the proposal deck and the architecture diagrams. This edition is the
practical first step toward it.
