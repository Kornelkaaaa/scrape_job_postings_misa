# MISA Opportunity Pipeline

Scrapes job postings (and later hackathons/conferences) relevant to MISA members
— West Virginia students — from a configurable list of sources, stores them in
SQLite, dedupes across runs, and generates a Markdown + HTML newsletter of
what's new.

**Location focus:** the global `include_locations` filter in `sources.yaml`
keeps jobs in West Virginia (Morgantown, Charleston, Clarksburg, ...) plus
anything explicitly remote. Remote-only boards (RemoteOK, WeWorkRemotely) opt
out with `include_locations: ["*"]` since every posting there is doable from WV.

**Where WV-local jobs come from:** WVU's job site (Taleo) and the WV state jobs
portal (NEOGOV/governmentjobs.com) render listings with JavaScript, so they
can't be scraped directly. Local coverage instead comes from two free APIs that
index them: Adzuna (`where: West Virginia`) and USAJOBS (federal employers are
big in WV — FBI CJIS in Clarksburg, NIOSH in Morgantown). Register both keys
to get local postings; without keys those sources are skipped and you'll only
see remote roles.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

## Usage

```bash
python scraper.py run                     # scrape all enabled sources
python scraper.py run --type job          # one opportunity type only
python scraper.py run --source Stripe     # one source only
python scraper.py list-new --since 7d     # what's new (also: 12h, 2w, --json)
python scraper.py newsletter --since 7d   # writes output/newsletter_<date>.{md,html}
python scraper.py sources                 # show configured sources
```

The newsletter HTML is email-client-safe (inline styles, table layout) — paste it
into Mailchimp/Substack/Gmail as-is, or share the Markdown in Slack/Discord.

## Adding a source

Edit `sources.yaml` — no code changes needed. Each entry has a `name`, `type`,
`opportunity_type` (`job`/`hackathon`/`conference`/`other`), and adapter-specific
`options`:

| type | works for | key options |
|---|---|---|
| `greenhouse` | companies whose careers page is `boards.greenhouse.io/<token>` | `board_token` |
| `lever` | companies at `jobs.lever.co/<slug>` | `company` |
| `rss` | any RSS/Atom feed | just `url` |
| `json_api` | any public JSON endpoint (e.g. RemoteOK, Devpost) | `items_path`, `fields` dot-paths |
| `html` | server-rendered career pages | `selectors` (CSS) |
| `adzuna` | Adzuna aggregator API | `country`, `what`, `where`, `company`; needs env keys |
| `usajobs` | USAJOBS federal API | `keyword`, `location_name`; needs env keys |
| `workday` | employers on `*.myworkdayjobs.com` (e.g. WVU Medicine) | `host`, `tenant`, `site`, `search_text` |

Ashby-hosted boards (OpenAI, Cohere, ...) work through `json_api` pointed at
`https://api.ashbyhq.com/posting-api/job-board/<company>`. Companies with no
public feed at all (KPMG, Deloitte, EY, CGI, Microsoft, WVU's Taleo site) are
covered via Adzuna `company:` searches instead.

Tips for official company sites: open the careers page, view source — many are
actually backed by Greenhouse/Lever/Ashby, in which case use that adapter (much
more reliable than HTML selectors). If jobs load via JavaScript, the `html`
adapter won't see them.

Set `enabled: false` to skip a source that starts blocking or breaking.

**Relevance filtering:** the global `filters:` block in `sources.yaml` keeps
junior/intern BA, consulting, and AI roles (whole-word match against
title/tags). Any source can override with its own
`include_keywords` / `exclude_keywords` (empty list = keep everything).

## API keys (both free, both recommended for WV coverage)

- **Adzuna:** register at https://developer.adzuna.com, set `ADZUNA_APP_ID`
  and `ADZUNA_APP_KEY`.
- **USAJOBS:** request a key at https://developer.usajobs.gov, set
  `USAJOBS_API_KEY` and `USAJOBS_EMAIL` (the email you registered with).

Set them locally as env vars and on GitHub as repo secrets. Sources with
missing keys are skipped with a warning, never crash the run.

## Deduplication

New rows are matched against the DB by normalized URL (https-forced, lowercased
host, tracking params like `utm_*` stripped), falling back to a hash of
title + org + posted_date when a source has no stable URLs. Re-running never
creates duplicates; `first_seen_at` records when we first saw each item.

## Scheduled runs

`.github/workflows/scrape.yml` runs every Monday 07:00 UTC (plus manual runs via
the Actions tab): tests → scrape → newsletter → commits `data/opportunities.db`
and `output/` back to the repo and uploads the newsletter as an artifact.
`data/` and `output/` are gitignored locally so your test runs don't create
noise, but CI force-adds them so results persist between weekly runs.

## Tests

```bash
pytest
```

Tests parse saved fixtures in `tests/fixtures/` — they never hit live sites.

## Politeness

- Descriptive User-Agent with contact address, 2s delay between requests
  (`settings.delay_seconds`).
- The `html` adapter checks robots.txt before fetching; API adapters
  (Greenhouse/Lever/Adzuna) use officially public endpoints.
- LinkedIn/Indeed are deliberately not scraped (their ToS prohibit it).

## Hackathons & conferences (Phase 2 — live)

Three event sources run alongside the job sources:

- **Devpost** (`devpost` adapter) — open/upcoming hackathons only;
  `posted_date` holds the *submission deadline*.
- **MLH** (`mlh` adapter) — season events page, parsed via its schema.org
  microdata (stable, unlike its CSS classes). Bump the season URL each summer.
- **WVU Events** (Localist JSON via `json_api`) — the campus calendar,
  keyword-filtered to tech/career/business events.

Event rules differ from jobs: global keyword/location filters apply **only to
job sources** (they'd kill titles like "HackWV 2026"); each event source sets
its own lists (nearby states + online). The newsletter automatically drops
events whose date has passed — jobs never expire this way.

## Roadmap

- Optional: direct email sending (Resend/SendGrid) instead of files-only.
- Possible extra event sources: confs.tech (JSON on GitHub), specific
  Eventbrite organizers (their public search API was discontinued).
