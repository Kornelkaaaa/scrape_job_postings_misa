# MISA Opportunity Pipeline

Scrapes job postings (and later hackathons/conferences) relevant to MISA members
from a configurable list of sources, stores them in SQLite, dedupes across runs,
and generates a Markdown + HTML newsletter of what's new.

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
| `adzuna` | Adzuna aggregator API | `country`, `what`; needs env keys |

Tips for official company sites: open the careers page, view source — many are
actually backed by Greenhouse/Lever/Ashby, in which case use that adapter (much
more reliable than HTML selectors). If jobs load via JavaScript, the `html`
adapter won't see them.

Set `enabled: false` to skip a source that starts blocking or breaking.

**Relevance filtering:** the global `filters:` block in `sources.yaml` keeps
junior/intern BA, consulting, and AI roles (whole-word match against
title/tags). Any source can override with its own
`include_keywords` / `exclude_keywords` (empty list = keep everything).

## Adzuna key (optional)

Register free at https://developer.adzuna.com, then set `ADZUNA_APP_ID` and
`ADZUNA_APP_KEY` (locally as env vars; on GitHub as repo secrets). Without them
the Adzuna source is skipped with a warning.

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

## Roadmap

- Phase 2: enable hackathon/conference sources (Devpost, MLH, Eventbrite) — the
  schema and newsletter already support them via `opportunity_type`.
- Optional: direct email sending (Resend/SendGrid) instead of files-only.
