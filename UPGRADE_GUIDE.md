# UPGRADE TO v0.3 — Scraper + Drafts Edition

This update adds **automatic news scraping** from 5 P1 competitor newsrooms,
plus a **drafts workflow** for analyst review before publishing.

## What's new

- **"Scrape latest news" button** in admin panel header — fetches recent
  articles from Accenture, TCS, Infosys, Wipro, and Capgemini newsrooms.
- **Drafts section** in admin panel — pre-filled signals appear here for
  analyst review. Click "Review & publish" to load into the form, add the
  4-bullet impact analysis, and publish.
- **Sources view removed** from the user-facing mobile app — users only
  see published signals now.
- **Existing signals preserved** — the database upgrade is non-destructive.

## Install steps

These steps preserve your existing signals (the `pulse.db` file).

### 1. Stop the running app

In your Command Prompt window where the app is running, press **Ctrl+C**.

### 2. Replace project files

Unzip the new package over your existing `cognizant-pulse-admin` folder,
replacing all files when prompted. Your `pulse.db` and `.env` files will
NOT be touched — only code files get replaced.

If you renamed the folder or moved it, place the new files there.

### 3. Install the new scraping library

In Command Prompt, navigate to your project folder if not already there:

```
cd Desktop\cognizant-pulse-admin
```

Then install:

```
python -m pip install -r requirements.txt
```

This downloads `beautifulsoup4` (the HTML parsing library used by the
scraper). All other libraries are already installed; pip will just confirm
them.

### 4. Restart the app

```
python main.py
```

You'll see a new line in the startup log:

```
Migration: added 'status' column to signal table
```

That's the non-destructive database upgrade — your existing signals are
now marked as "published" by default.

### 5. Hard-refresh your browser tabs

In both your mobile app tab and admin panel tab, press **Ctrl+F5** to
load the new HTML.

## How to use the scraper

Open the admin panel at `http://localhost:8000/admin` and log in.

Click the new **"Scrape latest news"** button (top right, teal-to-blue
gradient). The button shows a spinner while scraping runs (15-40 seconds
typical). When complete, a modal pops up showing the result:

- **New drafts** created — how many fresh articles became drafts
- **Duplicates skipped** — articles already in your database
- **Failed sources** — if any newsroom couldn't be scraped

After clicking "Got it", the new drafts appear in a yellow "Drafts
awaiting review" section above the published signals list. For each draft:

1. Click **"Review & publish"** — the form on the right loads with article
   basics pre-filled. The scraper has also suggested a signal type,
   topics, and impact level.
2. Review and edit the classification chips if needed.
3. Write the 4-bullet impact analysis (the strategic part the scraper
   can't do).
4. Click **"Publish signal"**. The draft becomes a published signal
   visible to all users.

Or click **"Discard"** to throw away a draft without publishing — useful
for articles that aren't competitively relevant.

## Known limitations

- Scrapers may need iteration after first run. Each competitor's HTML is
  parsed differently, and when a competitor redesigns their site (1-2
  times per year), the scraper for that source breaks until the parser
  is updated.
- If a scraper fails for one source, the others still work.
- The keyword-based classification suggestions are best-effort. The
  analyst should always verify before publishing.
- Scraping is triggered manually only. There's no automatic background
  scraping. (This is intentional — you stay in control of when activity
  happens, important for the legal review later.)

## What to ask Cognizant Legal before any pilot rollout

Automated scraping of competitor newsrooms is a gray area. The current
implementation is conservative (single request per source on manual
click, identifying User-Agent, no aggressive crawling), but legal review
is required before this goes beyond your laptop. Specifically ask:

- Is scraping public competitor newsroom HTML for internal CI use
  permissible under Cognizant policy?
- Are there specific competitors whose ToS prohibit it explicitly?
- Should we add a robots.txt check?

If legal raises concerns, you can disable the scraper button (it still
works as the analyst-curated tool from before).

## Reverting if needed

If anything goes wrong, you can restore the previous version by replacing
the project files with your older zip. Your `pulse.db` survives any
file-replacement step.
