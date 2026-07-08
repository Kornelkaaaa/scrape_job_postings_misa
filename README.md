# MISA Opportunity Pipeline

Scrapes jobs, internships, hackathons, and conferences relevant to MISA
members — West Virginia students — from a configurable list of sources,
stores them in SQLite, dedupes across runs, and generates a weekly
Markdown + HTML newsletter of what's new.

## The newsletter

Each issue is built from whatever is new in the chosen window and laid out as:

1. **Jump-to navigation** — section links with counts, plus category links
   under each section (anchor jumps work on GitHub/VS Code and most desktop
   email clients).
2. **🎓 WVU Career Fair Employers** — postings from companies on the
   `career_fair_orgs` list (both WVU fall fairs are loaded) float to the top.
3. **🎯 Internships & Co-ops** — job postings whose title matches
   `internship_keywords` (intern, co-op, trainee, summer analyst, ...) get
   their own section above everything else job-related.
4. **💼 Jobs** — grouped into topic categories (`categories:` in the config):
   AI/ML, Cybersecurity, Data & Analytics, Accounting & Finance,
   Business & Consulting, Software & IT, Other.
5. **🚀 Hackathons** — grouped by `hackathon_categories:` (In-Person first,
   then themes). Only shown while ≥ 5 days remain before the submission
   deadline (`MIN_LEAD_DAYS` in `pipeline/newsletter.py`).
6. **🎤 Conferences & Events** — campus calendars + regional tech conferences;
   past events are dropped automatically.

Free events carry a **Free** tag in their meta line (from MLH microdata,
Localist's `free` field, and Devpost's free-to-enter model; confs.tech has no
price data so those stay unlabeled).

The HTML is email-client-safe (inline styles, table layout) — paste into
Mailchimp/Gmail as-is. The Markdown renders nicely on GitHub/Slack/Discord.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # then fill in the API keys (see below)
```

## Usage

```bash
python scraper.py run                     # scrape all enabled sources
python scraper.py run --type hackathon    # one opportunity type only
python scraper.py run --source Stripe     # one source only
python scraper.py list-new --since 7d     # what's new (also: 12h, 2w, --json)
python scraper.py newsletter --since 7d   # writes output/newsletter_<date>.{md,html}
python scraper.py sources                 # show configured sources
```

## Where things come from

**Jobs.** Company ATS boards (Greenhouse/Lever/Ashby/Workday), remote boards
(RemoteOK, WeWorkRemotely), and two aggregator APIs. WVU's own job site
(Taleo) and the WV state portal (NEOGOV) are JavaScript-only and can't be
scraped — WV-local coverage comes from **Adzuna** (`where: West Virginia`,
plus per-company searches for KPMG/Deloitte/EY/CGI/Microsoft, which have no
public feeds) and **USAJOBS** (FBI CJIS in Clarksburg, NIOSH in Morgantown).

**Hackathons.** Devpost (open/upcoming only; the stored date is the
*submission deadline*) and MLH's season page (parsed via its schema.org
microdata — bump the season URL each summer). In-person events are kept only
in the travel region — WV, Ohio, Pittsburgh, DC (`hackathon_locations`
anchor in the config); online events always pass, except ones whose titles
mark them region-restricted (India/Africa/Europe/APAC/MENA).

**Conferences.** The WVU and Pitt campus calendars (Localist JSON APIs,
keyword-filtered to tech/career/business events) and confs.tech
(community conference data on GitHub) — the latter restricted to in-person
regional events, because its online listings are mostly paid foreign
conference brands with no price data to filter on.

## Adding a source

Edit `sources.yaml` — no code changes needed. Each entry has a `name`, `type`,
`opportunity_type` (`job`/`hackathon`/`conference`/`other`), and adapter-specific
`options`:

| type | works for | key options |
|---|---|---|
| `greenhouse` | companies whose careers page is `boards.greenhouse.io/<token>` | `board_token` |
| `lever` | companies at `jobs.lever.co/<slug>` | `company` |
| `rss` | any RSS/Atom feed (reads WWR-style `region` fields) | just `url` |
| `json_api` | any public JSON endpoint (RemoteOK, Ashby, Localist) | `items_path`, `fields` dot-paths, `flags` |
| `html` | server-rendered career pages | `selectors` (CSS) |
| `adzuna` | Adzuna aggregator API | `country`, `what`, `where`, `company`; needs env keys |
| `usajobs` | USAJOBS federal API | `keyword`, `location_name`; needs env keys |
| `workday` | employers on `*.myworkdayjobs.com` (e.g. WVU Medicine) | `host`, `tenant`, `site`, `search_text` |
| `devpost` | Devpost hackathons | `pages` |
| `mlh` | MLH season events page | `url` (season) |
| `confstech` | confs.tech conference data | `topics`, `years` |

Ashby-hosted boards (OpenAI, Cohere, ...) work through `json_api` pointed at
`https://api.ashbyhq.com/posting-api/job-board/<company>`. The `flags` option
turns boolean fields into tags (e.g. Localist's `free` → the Free label).

Tips for official company sites: open the careers page, view source — many are
actually backed by Greenhouse/Lever/Ashby/Workday, in which case use that
adapter (much more reliable than HTML selectors). If jobs load via JavaScript,
the `html` adapter won't see them.

Set `enabled: false` to skip a source that starts blocking or breaking.

## Filtering

The global `filters:` block keeps junior/intern BA, consulting, accounting,
tech, and AI roles (whole-word match against title/tags — descriptions are
deliberately ignored) located in WV or explicitly remote/US-reachable, and
excludes senior titles (senior/Sr/staff/principal/VP/chief/...).

**Global filters apply to job sources only** — they'd wrongly kill event
titles like "HackWV 2026". Event sources set their own per-source lists.
Any source can override any list; `include_locations: ["*"]` opts a source
out of location filtering entirely.

## API keys (both free, both needed for WV coverage)

- **Adzuna:** register at https://developer.adzuna.com → `ADZUNA_APP_ID`,
  `ADZUNA_APP_KEY`.
- **USAJOBS:** request at https://developer.usajobs.gov → `USAJOBS_API_KEY`,
  `USAJOBS_EMAIL` (must be the email you registered with).

Put them in `.env` locally (gitignored; loaded automatically) and as repo
secrets on GitHub. Sources with missing keys are skipped with a warning,
never crash the run. **Never put real keys in `.env.example`** — that file
is committed.

## Deduplication

New rows are matched against the DB by normalized URL (https-forced,
lowercased host, `utm_*` params stripped), falling back to a content hash
where URLs aren't stable (Adzuna serves one job under many ad ids — those
dedupe on title+org+location). Re-running never creates duplicates;
`first_seen_at` records when each item was first seen, and stored URLs are
refreshed on re-scrape so redirect links stay fresh.

## Scheduled runs

`.github/workflows/scrape.yml` runs every Monday 07:00 UTC (plus manual runs
via the Actions tab): tests → scrape → newsletter → commits
`data/opportunities.db` and `output/` back to the repo and uploads the
newsletter as an artifact. `data/` and `output/` are gitignored locally so
test runs don't create noise, but CI force-adds them so results persist
between weekly runs. **Pull before local work** — CI commits to `main`.

## Tests

```bash
pytest
```

Tests parse saved fixtures in `tests/fixtures/` — they never hit live sites.
Every Python module carries LEARNING NOTES comments explaining the concepts
it uses (dataclasses, regex word boundaries, SQL injection, polite scraping,
pagination, email HTML, ...).

## Politeness

- Descriptive User-Agent with contact address, 2s delay between requests
  (`settings.delay_seconds`).
- The `html` adapter checks robots.txt before fetching; API adapters use
  officially public endpoints.
- LinkedIn/Indeed are deliberately not scraped (their ToS prohibit it), and
  hiring.cafe's API is auth-locked — use those manually for discovery, then
  add the underlying company feeds here.

## Maintenance calendar

- **Each summer:** bump the MLH season URL in `sources.yaml`.
- **Each semester:** paste the new career-fair employer list into
  `career_fair_orgs`; manually check the no-feed employers (Alvarez & Marsal,
  Bravo Consulting, Trilogy, NextGen Federal, fbijobs.gov for FBI CJIS).
- **Anytime:** tune `filters:`, `categories:`, `internship_keywords`, or the
  `hackathon_locations` travel region — all config, no code.

## Roadmap

- Optional: direct email sending (Resend/SendGrid) instead of files-only.
- Possible: more nearby campus calendars (CMU, Ohio State — likely Localist),
  newsletter branding (navy/gold MISA layout — in progress separately).
