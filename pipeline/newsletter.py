"""Render new opportunities into a Markdown + HTML newsletter.

Two files come out of write_newsletter() for each format:
- newsletter_<date>.{md,html} - the TEASER. Short, curated, no navigation
  chrome. This is what you actually paste into Mailchimp/Substack/Gmail.
- newsletter_<date>_full.{md,html} - the ARCHIVE. Every opportunity, grouped
  into category subsections with a jump-to panel and pagination, for anyone
  who clicks "See all" from the teaser and wants to browse everything.

LEARNING NOTES:
- Email HTML is stuck in ~2003: no JS, and clients like Outlook strip <style>
  blocks - that's why the HTML here uses <table> layout and repeats
  style="..." inline on every element. The <style> block we DO include is
  progressive enhancement (mobile media query): clients that honor it get a
  nicer small-screen layout, clients that strip it (Outlook) just fall back
  to the inline styles, which already look fine.
- html.escape(): job titles come from the internet; one "<script>" or a
  stray "<" in a title would break (or attack) the page. ALWAYS escape
  external text before putting it into HTML.
- Separating render_*/render_full_* from write_newsletter keeps the
  renderers pure (text in -> text out), so tests can check output without
  touching the filesystem.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .models import Event

NAVY = "#122E5A"
GOLD = "#F0B30D"

# Section headings per opportunity type - the schema supports hackathons and
# conferences already, so the newsletter grows new sections automatically
# when Phase 2 sources are enabled.
TYPE_HEADINGS = {
    "job": "💼 Jobs & Internships",
    "hackathon": "🚀 Hackathons",
    "conference": "🎤 Conferences & Events",
    "other": "✨ Other Opportunities",
}
# URL-safe anchor ids, used by the archive page's jump-to nav and pagination
# (emoji don't belong in URLs)
TYPE_SLUGS = {"job": "jobs", "hackathon": "hackathons",
              "conference": "conferences", "other": "other"}
CAREER_FAIR_HEADING = "🎓 WVU Career Fair Employers"
EVENTS_HEADING = "📅 Upcoming MISA Events"
INTERNSHIPS_HEADING = "🎯 Internships & Co-ops"
JOBS_ONLY_HEADING = "💼 Jobs"  # used once internships have been split out above

SOCIAL_LABELS = {
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "email": "Email",
}

# opportunity types whose date means "when it happens" (vs "when posted") -
# these expire and must not be advertised after the fact
EVENT_TYPES = ("hackathon", "conference")

# Minimum days of runway an event needs to be worth advertising. A hackathon
# whose submission deadline is 4 days out isn't realistically joinable by
# newsletter readers; a conference can still be attended tomorrow.
MIN_LEAD_DAYS = {"hackathon": 5, "conference": 0}

# How many items the TEASER shows per opportunity type before linking out to
# the archive page instead of listing everything inline.
TEASER_LIMIT = 8

# The archive page's Jobs & Internships section still runs 100+ listings
# deep (AI alone can be 50+), so it's further chunked into pages there.
DEFAULT_JOB_PAGE_SIZE = 15


def _paginate(items: list, page_size: int | None) -> list[list]:
    """Split into `page_size`-item chunks. `page_size=None` (or a list that
    already fits in one page) returns a single page - callers always get at
    least one page, even for an empty list, so there's no special case."""
    if not page_size or len(items) <= page_size:
        return [items]
    return [items[i:i + page_size] for i in range(0, len(items), page_size)]


def _drop_past_events(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Never advertise a scraped hackathon/conference that's over - or too
    close to join.

    For event rows, posted_date holds the event/deadline date (see the
    devpost/mlh adapters). ISO dates compare correctly as plain strings.
    Rows without a date are kept - better to show a maybe-stale event than
    silently hide a live one.
    """
    today = date.today()
    kept = []
    for r in rows:
        opp_type = r["opportunity_type"]
        if opp_type not in EVENT_TYPES or not r["posted_date"]:
            kept.append(r)
            continue
        cutoff = (today + timedelta(days=MIN_LEAD_DAYS.get(opp_type, 0))).isoformat()
        if r["posted_date"] >= cutoff:
            kept.append(r)
    return kept


def is_career_fair_org(org: str, career_fair_orgs: list[str]) -> bool:
    """Whole-word match either way, so a config entry 'Deloitte' matches org
    'Deloitte Consulting LLP' and 'Leidos Inc.' matches 'Leidos' - but a short
    entry like 'EY' can't match inside unrelated names (Keyence, Harvey)."""
    org_c = org.strip()
    if not org_c:
        return False
    return any(
        re.search(rf"\b{re.escape(name.strip())}\b", org_c, re.I)
        or re.search(rf"\b{re.escape(org_c)}\b", name, re.I)
        for name in career_fair_orgs if name.strip()
    )


def _partition(rows: list[sqlite3.Row], career_fair_orgs: list[str]):
    """Split rows into (career-fair matches, everything else)."""
    fair = [r for r in rows if is_career_fair_org(r["org"], career_fair_orgs)]
    rest = [r for r in rows if not is_career_fair_org(r["org"], career_fair_orgs)]
    return fair, rest


def _group_by_type(rows: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    """{"job": [rows...], "hackathon": [rows...]} - setdefault creates each
    list the first time its key appears. Sections come out in TYPE_HEADINGS
    order (jobs first), not the incidental order rows arrived in."""
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(row["opportunity_type"], []).append(row)
    order = list(TYPE_HEADINGS)
    return dict(sorted(groups.items(),
                       key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))


def _is_internship(row: sqlite3.Row, keywords: list[str]) -> bool:
    haystack = f"{row['title']} {' '.join(json.loads(row['tags'] or '[]'))}"
    return any(re.search(rf"\b{re.escape(k)}\b", haystack, re.I) for k in keywords)


def _split_internships(items: list[sqlite3.Row],
                       internship_keywords: list[str] | None) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """(internships, remaining_jobs). Internships get their own section ABOVE
    the rest - they're the postings members care about most."""
    if not internship_keywords:
        return [], items
    internships = [r for r in items if _is_internship(r, internship_keywords)]
    if not internships:
        return [], items
    rest = [r for r in items if not _is_internship(r, internship_keywords)]
    return internships, rest


OTHER_CATEGORY = "✨ Other"


def categorize(row: sqlite3.Row, categories: dict) -> str:
    """First category whose keywords whole-word-match the title/tags wins.

    Order matters, and it comes straight from the YAML: python dicts preserve
    insertion order, so listing AI before Data & Analytics in sources.yaml
    means "AI Data Analyst" lands under AI.
    """
    haystack = f"{row['title']} {' '.join(json.loads(row['tags'] or '[]'))}"
    for name, keywords in categories.items():
        if any(re.search(rf"\b{re.escape(k)}\b", haystack, re.I) for k in keywords):
            return name
    return OTHER_CATEGORY


def _group_by_category(rows: list[sqlite3.Row], categories: dict) -> dict[str, list[sqlite3.Row]]:
    """Grouped in config order, empty categories dropped, Other last."""
    groups: dict[str, list[sqlite3.Row]] = {name: [] for name in categories}
    groups[OTHER_CATEGORY] = []
    for row in rows:
        groups[categorize(row, categories)].append(row)
    # dict comprehension that keeps only non-empty groups
    return {name: items for name, items in groups.items() if items}


def _upcoming_misa_events(events: list[Event] | None) -> list[Event]:
    """MISA meetings/events from config (not scraped) - drop ones already past."""
    if not events:
        return []
    today = date.today().isoformat()
    return [e for e in events if not e.date or e.date >= today]


def _event_meta(event: Event) -> str:
    parts = [p for p in [event.date, event.time, event.location] if p]
    return " · ".join(parts)


def _item_meta(row: sqlite3.Row) -> str:
    """The grey info line under a title: 'Org · Location · Date · tags'."""
    parts = [p for p in [row["org"], row["location"], row["posted_date"]] if p]
    tags = json.loads(row["tags"] or "[]")  # stored as JSON text in SQLite
    if tags:
        parts.append(", ".join(tags[:4]))   # at most 4 tags, keep it short
    return " · ".join(parts)


def _social_line_markdown(social: dict[str, str]) -> str:
    links = [f"[{SOCIAL_LABELS.get(key, key.title())}]({url})" for key, url in social.items() if url]
    return " · ".join(links)


def _social_footer_html(social: dict[str, str]) -> str:
    links = [
        f'<a href="{html.escape(url, quote=True)}" style="color:{GOLD};text-decoration:none;">'
        f'{html.escape(SOCIAL_LABELS.get(key, key.title()))}</a>'
        for key, url in social.items() if url
    ]
    if not links:
        return ""
    return f'<p style="font-size:13px;margin:0 0 8px;">{" &nbsp;·&nbsp; ".join(links)}</p>'


# --------------------------------------------------------------------------
# TEASER - the actual newsletter. Short, no jump-to nav, no pagination: big
# groups are truncated to TEASER_LIMIT (newest first) with a link out to the
# archive page instead of a wall of content.
# --------------------------------------------------------------------------

def _teaser_slice(items: list[sqlite3.Row]) -> tuple[list[sqlite3.Row], int]:
    """Newest-first top TEASER_LIMIT; returns (visible, total).

    Dedupes by (org, title) while picking the visible set - the same posting
    cross-listed under five counties would otherwise burn most of the 8 slots
    on one employer instead of showing variety. `total` still counts every
    row, duplicates included, so "See all N" stays accurate.
    """
    ordered = sorted(items, key=lambda r: r["posted_date"] or "", reverse=True)
    seen = set()
    visible = []
    for row in ordered:
        key = (row["org"].strip().lower(), row["title"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        visible.append(row)
        if len(visible) >= TEASER_LIMIT:
            break
    return visible, len(items)


def _md_items(row_list: list[sqlite3.Row]) -> list[str]:
    """Markdown bullet lines for a list of opportunity rows."""
    lines = []
    for row in row_list:
        # [text](url) is a Markdown link; ** makes it bold
        lines.append(f"- **[{row['title']}]({row['url']})**")
        meta = _item_meta(row)
        if meta:
            lines.append(f"  {meta}")  # two-space indent keeps it in the bullet
    return lines


def _md_event_items(events: list[Event]) -> list[str]:
    """Markdown bullet lines for MISA meetings/events."""
    lines = []
    for event in events:
        lines.append(f"- **{event.title}**")
        meta = _event_meta(event)
        if meta:
            lines.append(f"  {meta}")
        if event.description:
            lines.append(f"  {event.description}")
        if event.url:
            lines.append(f"  {event.url}")
    return lines


def _md_teaser_group(lines: list[str], heading: str, slug: str,
                     items: list[sqlite3.Row], full_list_href: str) -> None:
    """Appends one teaser section (heading + top items + See all/more) to
    `lines` in place. Skips entirely if `items` is empty (e.g. every job in
    the group turned out to be an internship, leaving nothing for Jobs)."""
    if not items:
        return
    visible, count = _teaser_slice(items)
    lines += [f"## {heading} ({count})", ""] + _md_items(visible)
    if count > len(visible):
        remaining = count - len(visible)
        if full_list_href:
            lines.append(f"[See all {count} →]({full_list_href}#{slug})")
        else:
            lines.append(f"...and {remaining} more.")
    lines.append("")


def render_markdown(rows: list[sqlite3.Row], since_label: str,
                    career_fair_orgs: list[str] | None = None,
                    intro: str = "",
                    events: list[Event] | None = None,
                    social: dict[str, str] | None = None,
                    full_list_href: str = "",
                    internship_keywords: list[str] | None = None) -> str:
    """The teaser: what actually gets sent. `full_list_href` is the archive
    Markdown file's name/URL - each truncated section links there; without
    it, truncated sections just say how many more there are (no link)."""
    today = date.today().isoformat()
    rows = _drop_past_events(rows)
    fair, rest = _partition(rows, career_fair_orgs or [])
    total = len(rows)
    lines = [
        f"# MISA Opportunities Newsletter — {today}",
        "",
        f"*{total} new opportunities this {since_label}.*",
        "",
    ]
    if intro:
        lines += [intro, ""]

    upcoming = _upcoming_misa_events(events)
    if upcoming:
        lines += [f"## {EVENTS_HEADING}", ""] + _md_event_items(upcoming) + [""]

    if fair:
        lines += [f"## {CAREER_FAIR_HEADING}", ""] + _md_items(fair) + [""]

    for opp_type, items in _group_by_type(rest).items():
        heading = TYPE_HEADINGS.get(opp_type, opp_type.title())
        if opp_type == "job":
            internships, items = _split_internships(items, internship_keywords)
            if internships:
                heading = JOBS_ONLY_HEADING
                _md_teaser_group(lines, INTERNSHIPS_HEADING, "internships",
                                 internships, full_list_href)
        _md_teaser_group(lines, heading, TYPE_SLUGS.get(opp_type, opp_type),
                         items, full_list_href)

    lines.append("---")
    social_line = _social_line_markdown(social or {})
    if social_line:
        lines.append(social_line)
    lines.append("*Generated automatically by the MISA opportunity pipeline.*")
    return "\n".join(lines)


def _html_teaser_item(row: sqlite3.Row, accent: str = NAVY) -> str:
    """One opportunity, whitespace-separated (no card border) - the 'not
    busy' look the boxed/bordered version didn't have."""
    meta = html.escape(_item_meta(row))
    return (
        '<div style="margin:0 0 16px;">'
        f'<a href="{html.escape(row["url"], quote=True)}" '
        f'style="font-size:15px;font-weight:600;color:{accent};text-decoration:none;">'
        f'{html.escape(row["title"])}</a>'
        f'<div style="font-size:13px;color:#6b7280;margin-top:2px;">{meta}</div>'
        "</div>"
    )


def _html_event_cards(events: list[Event]) -> str:
    """MISA meeting/event cards - gold date pill + title, kept visually
    distinct since these are the featured, curated content."""
    cards = []
    for event in events:
        meta = html.escape(_event_meta(event))
        title_html = html.escape(event.title)
        if event.url:
            title_html = (
                f'<a href="{html.escape(event.url, quote=True)}" '
                f'style="color:{NAVY};text-decoration:none;">{title_html}</a>'
            )
        desc = (f'<div style="font-size:13px;color:#374151;margin-top:4px;">'
                f'{html.escape(event.description)}</div>' if event.description else "")
        cards.append(
            '<div style="margin:0 0 16px;">'
            f'<div style="font-size:12px;font-weight:700;color:#ffffff;background:{GOLD};'
            f'display:inline-block;padding:2px 8px;border-radius:10px;">{meta}</div>'
            f'<div style="font-size:15px;font-weight:600;margin-top:6px;">{title_html}</div>'
            f'{desc}'
            "</div>"
        )
    return "".join(cards)


def _html_see_all(count: int, href: str) -> str:
    if not href:
        return ""
    return (
        f'<a href="{html.escape(href, quote=True)}" '
        f'style="display:inline-block;font-size:13px;font-weight:600;'
        f'color:{NAVY};text-decoration:none;">See all {count} →</a>'
    )


def _html_teaser_section(heading: str, inner_html: str) -> str:
    """Clean section heading with a thin gold underline instead of a boxed
    nav panel or bordered card table."""
    return (
        f'<h2 style="font-size:16px;color:{NAVY};margin:26px 0 12px;'
        f'padding-bottom:6px;border-bottom:2px solid {GOLD};">{html.escape(heading)}</h2>'
        f'{inner_html}'
    )


def _html_teaser_group(sections: list[str], heading: str, slug: str,
                       items: list[sqlite3.Row], full_list_href: str) -> None:
    """Appends one teaser section (as HTML) to `sections` in place. Skips
    entirely if `items` is empty (e.g. every job in the group turned out to
    be an internship, leaving nothing for Jobs)."""
    if not items:
        return
    visible, count = _teaser_slice(items)
    inner = "".join(_html_teaser_item(r) for r in visible)
    if count > len(visible):
        href = f"{full_list_href}#{slug}" if full_list_href else ""
        if href:
            inner += _html_see_all(count, href)
        else:
            inner += (f'<div style="font-size:13px;color:#6b7280;">'
                      f'...and {count - len(visible)} more.</div>')
    sections.append(_html_teaser_section(f"{heading} ({count})", inner))


def render_html(rows: list[sqlite3.Row], since_label: str,
                career_fair_orgs: list[str] | None = None,
                intro: str = "",
                events: list[Event] | None = None,
                social: dict[str, str] | None = None,
                full_list_href: str = "",
                internship_keywords: list[str] | None = None) -> str:
    """The teaser: what actually gets sent. `full_list_href` is the archive
    HTML file's name/URL - each truncated section links there; without it,
    truncated sections just say how many more there are (no link). Responsive
    for phone vs desktop via an embedded @media block - clients that strip
    <style> (older Outlook) just get the inline-styled fallback layout."""
    today = date.today().isoformat()
    rows = _drop_past_events(rows)
    fair, rest = _partition(rows, career_fair_orgs or [])
    total = len(rows)
    sections = []

    upcoming = _upcoming_misa_events(events)
    if upcoming:
        sections.append(_html_teaser_section(EVENTS_HEADING, _html_event_cards(upcoming)))

    if fair:
        sections.append(_html_teaser_section(
            CAREER_FAIR_HEADING,
            "".join(_html_teaser_item(r, accent=GOLD) for r in fair),
        ))

    for opp_type, items in _group_by_type(rest).items():
        heading = TYPE_HEADINGS.get(opp_type, opp_type.title())
        if opp_type == "job":
            internships, items = _split_internships(items, internship_keywords)
            if internships:
                heading = JOBS_ONLY_HEADING
                _html_teaser_group(sections, INTERNSHIPS_HEADING, "internships",
                                   internships, full_list_href)
        _html_teaser_group(sections, heading, TYPE_SLUGS.get(opp_type, opp_type),
                           items, full_list_href)

    intro_html = (f'<p style="font-size:14px;color:#374151;margin:16px 0 0;">{html.escape(intro)}</p>'
                  if intro else "")
    footer_social = _social_footer_html(social or {})

    # 600px centered white card on grey, navy header/footer bands - the
    # classic email layout that survives every mail client. The <style>
    # block is progressive enhancement only (see module notes).
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MISA Opportunities — {today}</title>
<style>
@media only screen and (max-width:600px) {{
  .misa-wrap {{ width:100% !important; }}
  .misa-pad {{ padding-left:20px !important; padding-right:20px !important; }}
}}
</style>
</head>
<body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" class="misa-wrap"
       style="background:#ffffff;border-radius:8px;overflow:hidden;max-width:600px;">
<tr><td class="misa-pad" style="background:{NAVY};padding:24px 32px;">
<span style="font-size:26px;font-weight:800;color:#ffffff;letter-spacing:0.5px;">M<span style="color:{GOLD};">i</span>SA</span>
<div style="font-size:12px;color:{GOLD};font-weight:600;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Opportunities Newsletter</div>
<p style="font-size:13px;color:#cbd5e1;margin:8px 0 0;">{today} — {total} new opportunities this {since_label}</p>
</td></tr>
<tr><td class="misa-pad" style="padding:24px 32px 8px;">
{intro_html}
{"".join(sections)}
</td></tr>
<tr><td class="misa-pad" style="background:{NAVY};padding:18px 32px;">
{footer_social}
<p style="font-size:11px;color:#9ca3af;margin:0;">Generated automatically by the MISA opportunity pipeline.</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""


# --------------------------------------------------------------------------
# ARCHIVE - everything, with category subsections, a jump-to panel, and
# Jobs & Internships pagination. Only linked to from the teaser's "See all".
# --------------------------------------------------------------------------

def _section_plan(rows: list[sqlite3.Row], career_fair_orgs: list[str],
                  type_categories: dict,
                  misa_events: list[Event] | None = None,
                  job_page_size: int | None = DEFAULT_JOB_PAGE_SIZE,
                  internship_keywords: list[str] | None = None) -> tuple[list[dict], int]:
    """Shared outline both archive renderers (and the nav) walk: a list of
    {slug, heading, items, subs, kind, pages} dicts. Computing this ONCE
    guarantees the navigation links and the actual sections can never
    disagree. `pages` is `items` chunked for pagination - a single page
    ([items]) for every section except Jobs & Internships, which is the one
    section that regularly runs long enough to need it."""
    rows = _drop_past_events(rows)
    fair, rest = _partition(rows, career_fair_orgs)
    plan = []
    upcoming = _upcoming_misa_events(misa_events)
    if upcoming:
        plan.append({"slug": "events", "heading": EVENTS_HEADING,
                     "items": upcoming, "subs": [], "kind": "events", "pages": [upcoming]})
    if fair:
        plan.append({"slug": "career-fair", "heading": CAREER_FAIR_HEADING,
                     "items": fair, "subs": [], "kind": "listing", "pages": [fair]})
    for opp_type, items in _group_by_type(rest).items():
        slug = TYPE_SLUGS.get(opp_type, opp_type)
        cats = type_categories.get(opp_type) or {}
        page_size = job_page_size if opp_type == "job" else None
        heading = TYPE_HEADINGS.get(opp_type, opp_type.title())
        if opp_type == "job":
            internships, items = _split_internships(items, internship_keywords)
            if internships:
                heading = JOBS_ONLY_HEADING  # no longer "& Internships" - they moved up
                # kept flat (no category subsections): a couple dozen
                # internships don't need them, but pagination still applies
                # if a semester's worth piles up.
                plan.append({"slug": "internships", "heading": INTERNSHIPS_HEADING,
                             "items": internships, "subs": [], "kind": "listing",
                             "pages": _paginate(internships, page_size)})
        subs = []
        if cats:
            for i, (name, cat_items) in enumerate(_group_by_category(items, cats).items()):
                sub_slug = f"{slug}-{i}"
                subs.append({"slug": sub_slug, "heading": name, "items": cat_items,
                             "pages": _paginate(cat_items, page_size)})
        if items:
            plan.append({"slug": slug, "heading": heading,
                         "items": items, "subs": subs, "kind": "listing",
                         "pages": _paginate(items, page_size)})
    return plan, len(rows)


def _md_pager(slug: str, page_num: int, total_pages: int) -> str:
    """'◀ Prev · Page 2 of 4 · Next ▶' - anchor jumps only, no JS. Every page
    stays in the document; this is a fast-forward, not a hide/show toggle."""
    parts = []
    if page_num > 1:
        parts.append(f"[◀ Prev](#{slug}-p{page_num - 1})")
    parts.append(f"Page {page_num} of {total_pages}")
    if page_num < total_pages:
        parts.append(f"[Next ▶](#{slug}-p{page_num + 1})")
    return " · ".join(parts)


def _md_paginated_items(slug: str, pages: list[list[sqlite3.Row]]) -> list[str]:
    """Item bullets for a (possibly multi-page) listing, with a pager and a
    per-page anchor when there's more than one page."""
    if len(pages) <= 1:
        return _md_items(pages[0] if pages else [])
    lines = []
    for i, chunk in enumerate(pages, start=1):
        lines += [f'<a id="{slug}-p{i}"></a>', "", _md_pager(slug, i, len(pages)), ""]
        lines += _md_items(chunk) + [""]
    return lines


def render_full_markdown(rows: list[sqlite3.Row], since_label: str,
                         career_fair_orgs: list[str] | None = None,
                         categories: dict | None = None,
                         hackathon_categories: dict | None = None,
                         intro: str = "",
                         events: list[Event] | None = None,
                         social: dict[str, str] | None = None,
                         job_page_size: int | None = DEFAULT_JOB_PAGE_SIZE,
                         internship_keywords: list[str] | None = None) -> str:
    """The archive: every opportunity, with category subsections, a jump-to
    panel, and pagination. Not sent directly - linked to from the teaser."""
    today = date.today().isoformat()
    # each opportunity type can have its own category scheme
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    plan, total = _section_plan(rows, career_fair_orgs or [], type_categories, events,
                                job_page_size, internship_keywords)
    lines = [
        f"# MISA Opportunities — Full List — {today}",
        "",
        f"*{total} new opportunities found in the last {since_label}.*",
        "",
    ]
    if intro:
        lines.append(intro)
        lines.append("")
    # Jump-to navigation. Raw <a id=...> anchors work on GitHub and in
    # VS Code; markdown heading auto-anchors vary per renderer, explicit
    # ids don't.
    if plan:
        nav = " · ".join(f"[{s['heading']} ({len(s['items'])})](#{s['slug']})" for s in plan)
        lines += [f"**Jump to:** {nav}", ""]
    for section in plan:
        lines += [f'<a id="{section["slug"]}"></a>', "", f"## {section['heading']}", ""]
        if section["kind"] == "events":
            lines += _md_event_items(section["items"]) + [""]
        elif section["subs"]:
            # per-section mini-nav so 100+ jobs are one click, not a scroll
            sub_nav = " · ".join(f"[{s['heading']} ({len(s['items'])})](#{s['slug']})"
                                 for s in section["subs"])
            lines += [sub_nav, ""]
            for sub in section["subs"]:
                lines += [f'<a id="{sub["slug"]}"></a>', "", f"### {sub['heading']}", ""]
                lines += _md_paginated_items(sub["slug"], sub["pages"]) + [""]
        else:
            lines += _md_paginated_items(section["slug"], section["pages"]) + [""]
    lines.append("---")
    social_line = _social_line_markdown(social or {})
    if social_line:
        lines.append(social_line)
    lines.append("*Generated automatically by the MISA opportunity pipeline.*")
    return "\n".join(lines)


def _html_cards(items: list[sqlite3.Row], accent: str = NAVY) -> str:
    """One <table> of job 'cards'. Tables, not divs - see module notes."""
    cards = []
    for row in items:
        meta = html.escape(_item_meta(row))
        cards.append(
            '<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;">'
            f'<a href="{html.escape(row["url"], quote=True)}" '
            f'style="font-size:16px;font-weight:600;color:{accent};text-decoration:none;">'
            f'{html.escape(row["title"])}</a>'
            f'<div style="font-size:13px;color:#6b7280;margin-top:2px;">{meta}</div>'
            "</td></tr>"
        )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{"".join(cards)}</table>')


def _html_event_cards_table(events: list[Event]) -> str:
    """Table-row variant of the event cards, used on the archive page where
    everything else is table-based too."""
    cards = []
    for event in events:
        meta = html.escape(_event_meta(event))
        title_html = html.escape(event.title)
        if event.url:
            title_html = (
                f'<a href="{html.escape(event.url, quote=True)}" '
                f'style="color:{NAVY};text-decoration:none;">{title_html}</a>'
            )
        desc = (f'<div style="font-size:13px;color:#374151;margin-top:4px;">'
                f'{html.escape(event.description)}</div>' if event.description else "")
        cards.append(
            '<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">'
            f'<div style="font-size:12px;font-weight:700;color:#ffffff;background:{GOLD};'
            f'display:inline-block;padding:2px 8px;border-radius:10px;">{meta}</div>'
            f'<div style="font-size:16px;font-weight:600;margin-top:6px;">{title_html}</div>'
            f'{desc}'
            "</td></tr>"
        )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{"".join(cards)}</table>')


def _html_pager(slug: str, page_num: int, total_pages: int) -> str:
    """'◀ Prev · Page 2 of 4 · Next ▶' - anchor jumps only, no JS. Every page
    stays in the document; this is a fast-forward, not a hide/show toggle."""
    link = ('<a href="#{slug}-p{n}" style="color:{color};text-decoration:none;'
            'font-size:12px;">{label}</a>')
    parts = []
    if page_num > 1:
        parts.append(link.format(slug=slug, n=page_num - 1, color=NAVY, label="◀ Prev"))
    parts.append(f'<span style="font-size:12px;color:#6b7280;">Page {page_num} of {total_pages}</span>')
    if page_num < total_pages:
        parts.append(link.format(slug=slug, n=page_num + 1, color=NAVY, label="Next ▶"))
    return '<div style="margin:8px 0;">' + " &nbsp;·&nbsp; ".join(parts) + "</div>"


def _html_paginated_cards(slug: str, pages: list[list[sqlite3.Row]], accent: str = NAVY) -> str:
    """Item cards for a (possibly multi-page) listing, with a pager and a
    per-page anchor when there's more than one page."""
    if len(pages) <= 1:
        return _html_cards(pages[0] if pages else [], accent)
    parts = []
    for i, chunk in enumerate(pages, start=1):
        parts.append(f'<a id="{slug}-p{i}"></a>')
        parts.append(_html_pager(slug, i, len(pages)))
        parts.append(_html_cards(chunk, accent))
    return "".join(parts)


def _html_section(section: dict, accent: str = NAVY) -> str:
    """One section from the plan: anchored h2, optional anchored h3 subs."""
    parts = [f'<h2 id="{section["slug"]}" '
             f'style="font-size:18px;color:#111827;margin:28px 0 4px;">'
             f'{html.escape(section["heading"])}</h2>']
    if section["kind"] == "events":
        parts.append(_html_event_cards_table(section["items"]))
    elif section["subs"]:
        for sub in section["subs"]:
            parts.append(f'<h3 id="{sub["slug"]}" '
                         f'style="font-size:15px;color:#374151;margin:18px 0 2px;">'
                         f'{html.escape(sub["heading"])}</h3>')
            parts.append(_html_paginated_cards(sub["slug"], sub["pages"], accent))
    else:
        parts.append(_html_paginated_cards(section["slug"], section["pages"], accent))
    return "".join(parts)


def _html_nav(plan: list[dict]) -> str:
    """Jump-to panel under the header: one line per section (bold link with
    count), followed by its category links. Anchor jumps work in most
    desktop/webmail clients; where unsupported they render as plain text."""
    link = ('<a href="#{slug}" style="color:{color};text-decoration:none;'
            'font-size:13px;{extra}">{label}</a>')
    rows_html = []
    for section in plan:
        parts = [link.format(slug=section["slug"], color=NAVY, extra="font-weight:600;",
                             label=f'{html.escape(section["heading"])} ({len(section["items"])})')]
        parts += [link.format(slug=sub["slug"], color=NAVY, extra="",
                              label=f'{html.escape(sub["heading"])} ({len(sub["items"])})')
                  for sub in section["subs"]]
        rows_html.append('<div style="margin:3px 0;">' + " &nbsp;·&nbsp; ".join(parts) + "</div>")
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            '<tr><td style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;'
            'padding:12px 16px;margin-top:16px;">'
            '<div style="font-size:12px;color:#6b7280;text-transform:uppercase;'
            'letter-spacing:0.05em;margin-bottom:6px;">Jump to</div>'
            + "".join(rows_html) + "</td></tr></table>")


def render_full_html(rows: list[sqlite3.Row], since_label: str,
                     career_fair_orgs: list[str] | None = None,
                     categories: dict | None = None,
                     hackathon_categories: dict | None = None,
                     intro: str = "",
                     events: list[Event] | None = None,
                     social: dict[str, str] | None = None,
                     job_page_size: int | None = DEFAULT_JOB_PAGE_SIZE,
                     internship_keywords: list[str] | None = None) -> str:
    """The archive: every opportunity, with category subsections, a jump-to
    panel, and pagination. Not sent directly - linked to from the teaser."""
    today = date.today().isoformat()
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    plan, total = _section_plan(rows, career_fair_orgs or [], type_categories, events,
                                job_page_size, internship_keywords)
    sections = []
    if plan:
        sections.append(_html_nav(plan))
    for section in plan:
        # WVU gold accent for employers members can meet in person
        accent = GOLD if section["slug"] == "career-fair" else NAVY
        sections.append(_html_section(section, accent))

    intro_html = (f'<p style="font-size:14px;color:#374151;margin:16px 0 0;">{html.escape(intro)}</p>'
                  if intro else "")
    footer_social = _social_footer_html(social or {})

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>MISA Opportunities — Full List — {today}</title></head>
<body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;">
<tr><td style="background:{NAVY};padding:24px 32px;">
<span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:0.5px;">M<span style="color:{GOLD};">i</span>SA</span>
<div style="font-size:13px;color:{GOLD};font-weight:600;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Full Opportunity List</div>
<p style="font-size:13px;color:#cbd5e1;margin:8px 0 0;">{today} — {total} new opportunities in the last {since_label}</p>
</td></tr>
<tr><td style="padding:0 32px 32px;">
{intro_html}
{"".join(sections)}
</td></tr>
<tr><td style="background:{NAVY};padding:20px 32px;">
{footer_social}
<p style="font-size:12px;color:#9ca3af;margin:0;">Generated automatically by the MISA opportunity pipeline.</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def write_newsletter(rows: list[sqlite3.Row], output_dir: str | Path,
                     since_label: str,
                     career_fair_orgs: list[str] | None = None,
                     categories: dict | None = None,
                     hackathon_categories: dict | None = None,
                     intro: str = "",
                     events: list[Event] | None = None,
                     social: dict[str, str] | None = None,
                     job_page_size: int | None = DEFAULT_JOB_PAGE_SIZE,
                     archive_base_url: str = "",
                     internship_keywords: list[str] | None = None) -> tuple[Path, Path]:
    """Writes the teaser (what you actually send) plus a companion "_full"
    archive page with everything, and returns the teaser's (md_path,
    html_path). `archive_base_url` (e.g. a GitHub Pages URL) makes the
    teaser's "See all" links real absolute URLs; without it, they fall back
    to a relative filename that only resolves when both files travel
    together (e.g. attached side by side, or opened from the same folder)."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    md_path = out / f"newsletter_{stamp}.md"
    html_path = out / f"newsletter_{stamp}.html"
    full_md_path = out / f"newsletter_{stamp}_full.md"
    full_html_path = out / f"newsletter_{stamp}_full.html"

    full_md_path.write_text(
        render_full_markdown(rows, since_label, career_fair_orgs, categories,
                             hackathon_categories, intro, events, social, job_page_size,
                             internship_keywords),
        encoding="utf-8",
    )
    full_html_path.write_text(
        render_full_html(rows, since_label, career_fair_orgs, categories,
                         hackathon_categories, intro, events, social, job_page_size,
                         internship_keywords),
        encoding="utf-8",
    )
    md_href = f"{archive_base_url}/{full_md_path.name}" if archive_base_url else full_md_path.name
    html_href = f"{archive_base_url}/{full_html_path.name}" if archive_base_url else full_html_path.name
    md_path.write_text(
        render_markdown(rows, since_label, career_fair_orgs, intro, events, social,
                        full_list_href=md_href, internship_keywords=internship_keywords),
        encoding="utf-8",
    )
    html_path.write_text(
        render_html(rows, since_label, career_fair_orgs, intro, events, social,
                   full_list_href=html_href, internship_keywords=internship_keywords),
        encoding="utf-8",
    )
    return md_path, html_path
