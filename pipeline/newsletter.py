"""Render new opportunities into a Markdown + HTML newsletter.

Files-only delivery: paste the HTML into Mailchimp/Substack/Gmail, or share
the Markdown directly (Slack/Discord/Notion).

Three kinds of file come out of write_newsletter() for each format:
- newsletter_<date>.{md,html} - the TEASER. Short, curated, no navigation
  chrome. This is what you actually paste into Mailchimp/Substack/Gmail.
- newsletter_<date>_full.{md,html} - the HUB. A browse-everything landing
  page: each top-level section shows its newest ~10 items with a "See all N"
  button, plus a jump-to panel across sections.
- newsletter_<date>_full_<slug>.{md,html} - one SECTION PAGE per top-level
  section (jobs, internships, hackathons, ...). The complete list for that
  one section - category subsections intact, no pagination - which is what
  the teaser's and hub's "See all" buttons open.

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
- Separating render_*/render_full_*/render_section_* from write_newsletter
  keeps the renderers pure (text in -> text out), so tests can check output
  without touching the filesystem.
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
# URL-safe anchor/filename ids, used by the hub's jump-to nav and to name the
# per-section pages (emoji don't belong in URLs or filenames)
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
# the section page instead of listing everything inline.
TEASER_LIMIT = 8

# How many items the HUB (_full landing page) previews per top-level section
# before its "See all N" button links out to that section's own page.
HUB_LIMIT = 10


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


def _section_href(base: str, slug: str, ext: str) -> str:
    """URL of a single section's own page, e.g. 'newsletter_..._full_jobs.html'.
    `base` is everything up to '_full' (relative filename or absolute Pages
    URL); the renderer appends '_<slug>.<ext>'. Empty base -> empty string, in
    which case callers fall back to a plain "...and N more" with no link."""
    return f"{base}_{slug}.{ext}" if base else ""


def _top_slice(items: list[sqlite3.Row], limit: int) -> tuple[list[sqlite3.Row], int]:
    """Newest-first top `limit`; returns (visible, total).

    Dedupes by (org, title) while picking the visible set - the same posting
    cross-listed under five counties would otherwise burn most of the slots
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
        if len(visible) >= limit:
            break
    return visible, len(items)


# --------------------------------------------------------------------------
# TEASER - the actual newsletter. Short, no jump-to nav: big groups are
# truncated to TEASER_LIMIT (newest first) with a "See all" link out to that
# section's own page instead of a wall of content.
# --------------------------------------------------------------------------

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
                     items: list[sqlite3.Row], section_href_base: str) -> None:
    """Appends one teaser section (heading + top items + See all/more) to
    `lines` in place. Skips entirely if `items` is empty (e.g. every job in
    the group turned out to be an internship, leaving nothing for Jobs)."""
    if not items:
        return
    visible, count = _top_slice(items, TEASER_LIMIT)
    lines += [f"## {heading} ({count})", ""] + _md_items(visible)
    if count > len(visible):
        href = _section_href(section_href_base, slug, "md")
        if href:
            lines.append(f"[See all {count} →]({href})")
        else:
            lines.append(f"...and {count - len(visible)} more.")
    lines.append("")


def render_markdown(rows: list[sqlite3.Row], since_label: str,
                    career_fair_orgs: list[str] | None = None,
                    intro: str = "",
                    events: list[Event] | None = None,
                    social: dict[str, str] | None = None,
                    section_href_base: str = "",
                    internship_keywords: list[str] | None = None) -> str:
    """The teaser: what actually gets sent. `section_href_base` is everything
    up to '_full' in the section pages' URL; each truncated section links to
    its own '..._full_<slug>.md' page. Without it, truncated sections just say
    how many more there are (no link)."""
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
                                 internships, section_href_base)
        _md_teaser_group(lines, heading, TYPE_SLUGS.get(opp_type, opp_type),
                         items, section_href_base)

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


def _html_teaser_section(heading: str, inner_html: str, anchor: str = "") -> str:
    """Clean section heading with a thin gold underline instead of a boxed
    nav panel or bordered card table. `anchor` adds an id for jump-to nav
    (used by the hub); the teaser leaves it blank."""
    id_attr = f' id="{anchor}"' if anchor else ""
    return (
        f'<h2{id_attr} style="font-size:16px;color:{NAVY};margin:26px 0 12px;'
        f'padding-bottom:6px;border-bottom:2px solid {GOLD};">{html.escape(heading)}</h2>'
        f'{inner_html}'
    )


def _html_teaser_group(sections: list[str], heading: str, slug: str,
                       items: list[sqlite3.Row], section_href_base: str) -> None:
    """Appends one teaser section (as HTML) to `sections` in place. Skips
    entirely if `items` is empty (e.g. every job in the group turned out to
    be an internship, leaving nothing for Jobs)."""
    if not items:
        return
    visible, count = _top_slice(items, TEASER_LIMIT)
    inner = "".join(_html_teaser_item(r) for r in visible)
    if count > len(visible):
        href = _section_href(section_href_base, slug, "html")
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
                section_href_base: str = "",
                internship_keywords: list[str] | None = None) -> str:
    """The teaser: what actually gets sent. `section_href_base` is everything
    up to '_full' in the section pages' URL; each truncated section links to
    its own '..._full_<slug>.html' page. Without it, truncated sections just
    say how many more there are (no link). Responsive for phone vs desktop via
    an embedded @media block - clients that strip <style> (older Outlook) just
    get the inline-styled fallback layout."""
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
                                   internships, section_href_base)
        _html_teaser_group(sections, heading, TYPE_SLUGS.get(opp_type, opp_type),
                           items, section_href_base)

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
# SHARED OUTLINE - the section plan both the hub and the section pages walk,
# so the navigation, the "See all" links, and the actual pages can never
# disagree about which sections exist or how many items each has.
# --------------------------------------------------------------------------

def _section_plan(rows: list[sqlite3.Row], career_fair_orgs: list[str],
                  type_categories: dict,
                  misa_events: list[Event] | None = None,
                  internship_keywords: list[str] | None = None) -> tuple[list[dict], int]:
    """A list of {slug, heading, items, subs, kind} dicts, in display order.
    `subs` is the category breakdown (AI/ML, Cybersecurity, ...) for sections
    that have one, else []. Computed ONCE so nav and content stay in sync."""
    rows = _drop_past_events(rows)
    fair, rest = _partition(rows, career_fair_orgs)
    plan = []
    upcoming = _upcoming_misa_events(misa_events)
    if upcoming:
        plan.append({"slug": "events", "heading": EVENTS_HEADING,
                     "items": upcoming, "subs": [], "kind": "events"})
    if fair:
        plan.append({"slug": "career-fair", "heading": CAREER_FAIR_HEADING,
                     "items": fair, "subs": [], "kind": "listing"})
    for opp_type, items in _group_by_type(rest).items():
        slug = TYPE_SLUGS.get(opp_type, opp_type)
        cats = type_categories.get(opp_type) or {}
        heading = TYPE_HEADINGS.get(opp_type, opp_type.title())
        if opp_type == "job":
            internships, items = _split_internships(items, internship_keywords)
            if internships:
                heading = JOBS_ONLY_HEADING  # no longer "& Internships" - they moved up
                # kept flat (no category subsections): a couple dozen
                # internships don't need them.
                plan.append({"slug": "internships", "heading": INTERNSHIPS_HEADING,
                             "items": internships, "subs": [], "kind": "listing"})
        subs = []
        if cats:
            for i, (name, cat_items) in enumerate(_group_by_category(items, cats).items()):
                subs.append({"slug": f"{slug}-{i}", "heading": name, "items": cat_items})
        if items:
            plan.append({"slug": slug, "heading": heading,
                         "items": items, "subs": subs, "kind": "listing"})
    return plan, len(rows)


def _plan_section(rows, career_fair_orgs, type_categories, misa_events,
                  internship_keywords, slug):
    """The single plan section with this slug, or None if absent."""
    plan, _ = _section_plan(rows, career_fair_orgs, type_categories,
                            misa_events, internship_keywords)
    return next((s for s in plan if s["slug"] == slug), None)


# --------------------------------------------------------------------------
# HUB - the _full landing page. Every top-level section, but only its newest
# HUB_LIMIT items, each with a "See all N" button that opens the section page.
# A jump-to panel links to each section block within this page.
# --------------------------------------------------------------------------

def render_full_markdown(rows: list[sqlite3.Row], since_label: str,
                         career_fair_orgs: list[str] | None = None,
                         categories: dict | None = None,
                         hackathon_categories: dict | None = None,
                         intro: str = "",
                         events: list[Event] | None = None,
                         social: dict[str, str] | None = None,
                         section_href_base: str = "",
                         internship_keywords: list[str] | None = None) -> str:
    """The hub: newest HUB_LIMIT items per top-level section, each linking out
    to its own section page. Not sent directly - linked from the teaser."""
    today = date.today().isoformat()
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    plan, total = _section_plan(rows, career_fair_orgs or [], type_categories,
                                events, internship_keywords)
    lines = [
        f"# MISA Opportunities — Full List — {today}",
        "",
        f"*{total} new opportunities found in the last {since_label}.*",
        "",
    ]
    if intro:
        lines += [intro, ""]
    # Jump-to navigation. Raw <a id=...> anchors work on GitHub and in
    # VS Code; markdown heading auto-anchors vary per renderer, explicit
    # ids don't.
    if plan:
        nav = " · ".join(f"[{s['heading']} ({len(s['items'])})](#{s['slug']})" for s in plan)
        lines += [f"**Jump to:** {nav}", ""]
    for section in plan:
        lines += [f'<a id="{section["slug"]}"></a>', "",
                  f"## {section['heading']} ({len(section['items'])})", ""]
        if section["kind"] == "events":
            lines += _md_event_items(section["items"]) + [""]
            continue
        visible, count = _top_slice(section["items"], HUB_LIMIT)
        lines += _md_items(visible)
        if count > len(visible):
            href = _section_href(section_href_base, section["slug"], "md")
            if href:
                lines.append(f"[See all {count} →]({href})")
            else:
                lines.append(f"...and {count - len(visible)} more.")
        lines.append("")
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
    """Table-row variant of the event cards, used on the archive pages where
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


def _html_hub_nav(plan: list[dict]) -> str:
    """Jump-to panel under the header: one link per top-level section (with
    count) to its block on this hub page."""
    link = ('<a href="#{slug}" style="color:{color};text-decoration:none;'
            'font-size:13px;font-weight:600;">{label}</a>')
    parts = [link.format(slug=s["slug"], color=NAVY,
                         label=f'{html.escape(s["heading"])} ({len(s["items"])})')
             for s in plan]
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            '<tr><td style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;'
            'padding:12px 16px;margin-top:16px;">'
            '<div style="font-size:12px;color:#6b7280;text-transform:uppercase;'
            'letter-spacing:0.05em;margin-bottom:6px;">Jump to</div>'
            '<div style="margin:3px 0;">' + " &nbsp;·&nbsp; ".join(parts)
            + "</div></td></tr></table>")


def _html_shell(subtitle: str, meta_line: str, inner_html: str,
                social: dict[str, str], width: int = 600) -> str:
    """The navy-banded white card that wraps the hub and section pages."""
    footer_social = _social_footer_html(social or {})
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>MISA Opportunities — {html.escape(subtitle)}</title></head>
<body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="{width}" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;max-width:{width}px;">
<tr><td style="background:{NAVY};padding:24px 32px;">
<span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:0.5px;">M<span style="color:{GOLD};">i</span>SA</span>
<div style="font-size:13px;color:{GOLD};font-weight:600;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">{html.escape(subtitle)}</div>
<p style="font-size:13px;color:#cbd5e1;margin:8px 0 0;">{meta_line}</p>
</td></tr>
<tr><td style="padding:16px 32px 32px;">
{inner_html}
</td></tr>
<tr><td style="background:{NAVY};padding:20px 32px;">
{footer_social}
<p style="font-size:12px;color:#9ca3af;margin:0;">Generated automatically by the MISA opportunity pipeline.</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def render_full_html(rows: list[sqlite3.Row], since_label: str,
                     career_fair_orgs: list[str] | None = None,
                     categories: dict | None = None,
                     hackathon_categories: dict | None = None,
                     intro: str = "",
                     events: list[Event] | None = None,
                     social: dict[str, str] | None = None,
                     section_href_base: str = "",
                     internship_keywords: list[str] | None = None) -> str:
    """The hub: newest HUB_LIMIT items per top-level section, each linking out
    to its own section page. Not sent directly - linked from the teaser."""
    today = date.today().isoformat()
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    plan, total = _section_plan(rows, career_fair_orgs or [], type_categories,
                                events, internship_keywords)
    intro_html = (f'<p style="font-size:14px;color:#374151;margin:16px 0 0;">{html.escape(intro)}</p>'
                  if intro else "")
    blocks = [intro_html]
    if plan:
        blocks.append(_html_hub_nav(plan))
    for section in plan:
        accent = GOLD if section["slug"] == "career-fair" else NAVY
        heading = f'{section["heading"]} ({len(section["items"])})'
        if section["kind"] == "events":
            inner = _html_event_cards_table(section["items"])
        else:
            visible, count = _top_slice(section["items"], HUB_LIMIT)
            inner = "".join(_html_teaser_item(r, accent) for r in visible)
            if count > len(visible):
                href = _section_href(section_href_base, section["slug"], "html")
                if href:
                    inner += _html_see_all(count, href)
                else:
                    inner += (f'<div style="font-size:13px;color:#6b7280;">'
                              f'...and {count - len(visible)} more.</div>')
        blocks.append(_html_teaser_section(heading, inner, anchor=section["slug"]))

    meta = f"{today} — {total} new opportunities in the last {since_label}"
    return _html_shell("Full Opportunity List", meta, "".join(blocks), social or {})


# --------------------------------------------------------------------------
# SECTION PAGE - one top-level section in full. Category subsections intact,
# no pagination: this is the "See all" destination for both teaser and hub.
# --------------------------------------------------------------------------

def _md_section_body(section: dict) -> list[str]:
    """The full listing for one section (no top-level heading; the caller's
    title/header already names it). Category subsections rendered in full."""
    if section["kind"] == "events":
        return _md_event_items(section["items"])
    if section["subs"]:
        lines = []
        sub_nav = " · ".join(f"[{s['heading']} ({len(s['items'])})](#{s['slug']})"
                             for s in section["subs"])
        lines += [sub_nav, ""]
        for sub in section["subs"]:
            lines += [f'<a id="{sub["slug"]}"></a>', "", f"### {sub['heading']}", ""]
            lines += _md_items(sub["items"]) + [""]
        return lines
    return _md_items(section["items"])


def _html_section_body(section: dict, accent: str = NAVY) -> str:
    """The full listing for one section as HTML (no top-level heading)."""
    if section["kind"] == "events":
        return _html_event_cards_table(section["items"])
    if section["subs"]:
        parts = []
        for sub in section["subs"]:
            parts.append(f'<h3 id="{sub["slug"]}" '
                         f'style="font-size:15px;color:#374151;margin:18px 0 2px;">'
                         f'{html.escape(sub["heading"])}</h3>')
            parts.append(_html_cards(sub["items"], accent))
        return "".join(parts)
    return _html_cards(section["items"], accent)


def render_section_markdown(rows: list[sqlite3.Row], since_label: str, slug: str,
                            career_fair_orgs: list[str] | None = None,
                            categories: dict | None = None,
                            hackathon_categories: dict | None = None,
                            events: list[Event] | None = None,
                            social: dict[str, str] | None = None,
                            internship_keywords: list[str] | None = None,
                            hub_href: str = "") -> str:
    """One section's own page: every item in it, category subsections intact.
    `hub_href` is a back-link to the hub. Returns a short placeholder page if
    the section turned out to be empty (nothing matched `slug`)."""
    today = date.today().isoformat()
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    section = _plan_section(rows, career_fair_orgs or [], type_categories,
                            events, internship_keywords, slug)
    back = f"[← Back to all opportunities]({hub_href})" if hub_href else ""
    if not section:
        lines = [f"# MISA Opportunities — {today}", ""]
        if back:
            lines += [back, ""]
        lines.append("*Nothing in this section right now.*")
        return "\n".join(lines)
    lines = [
        f"# {section['heading']} — {today}",
        "",
        f"*{len(section['items'])} opportunities, found in the last {since_label}.*",
        "",
    ]
    if back:
        lines += [back, ""]
    lines += _md_section_body(section)
    lines.append("---")
    social_line = _social_line_markdown(social or {})
    if social_line:
        lines.append(social_line)
    lines.append("*Generated automatically by the MISA opportunity pipeline.*")
    return "\n".join(lines)


def render_section_html(rows: list[sqlite3.Row], since_label: str, slug: str,
                        career_fair_orgs: list[str] | None = None,
                        categories: dict | None = None,
                        hackathon_categories: dict | None = None,
                        events: list[Event] | None = None,
                        social: dict[str, str] | None = None,
                        internship_keywords: list[str] | None = None,
                        hub_href: str = "") -> str:
    """One section's own page as HTML: every item, subsections intact."""
    today = date.today().isoformat()
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    section = _plan_section(rows, career_fair_orgs or [], type_categories,
                            events, internship_keywords, slug)
    back = ""
    if hub_href:
        back = (f'<p style="margin:16px 0 0;"><a href="{html.escape(hub_href, quote=True)}" '
                f'style="font-size:13px;font-weight:600;color:{NAVY};text-decoration:none;">'
                f'← Back to all opportunities</a></p>')
    if not section:
        return _html_shell("Opportunities", today,
                           back + '<p style="font-size:14px;color:#374151;">'
                           'Nothing in this section right now.</p>', social or {})
    accent = GOLD if section["slug"] == "career-fair" else NAVY
    meta = (f"{today} — {len(section['items'])} opportunities "
            f"in the last {since_label}")
    inner = back + _html_section_body(section, accent)
    return _html_shell(section["heading"], meta, inner, social or {})


def write_newsletter(rows: list[sqlite3.Row], output_dir: str | Path,
                     since_label: str,
                     career_fair_orgs: list[str] | None = None,
                     categories: dict | None = None,
                     hackathon_categories: dict | None = None,
                     intro: str = "",
                     events: list[Event] | None = None,
                     social: dict[str, str] | None = None,
                     archive_base_url: str = "",
                     internship_keywords: list[str] | None = None) -> tuple[Path, Path]:
    """Writes the teaser (what you actually send), a companion "_full" hub
    page, and one "_full_<slug>" page per top-level section, then returns the
    teaser's (md_path, html_path). `archive_base_url` (e.g. a GitHub Pages
    URL) makes the "See all" links real absolute URLs; without it, they fall
    back to relative filenames that only resolve when the files travel
    together (opened from the same folder)."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    md_path = out / f"newsletter_{stamp}.md"
    html_path = out / f"newsletter_{stamp}.html"

    # Everything up to '_full', shared by the hub file and every section page.
    # Relative filename by default; absolute Pages URL when configured.
    stem = f"newsletter_{stamp}_full"
    base = f"{archive_base_url}/{stem}" if archive_base_url else stem

    kw = dict(career_fair_orgs=career_fair_orgs, categories=categories,
              hackathon_categories=hackathon_categories, events=events,
              social=social, internship_keywords=internship_keywords)

    # Hub (_full) - links each section's "See all" out via `base`.
    (out / f"{stem}.md").write_text(
        render_full_markdown(rows, since_label, intro=intro, section_href_base=base, **kw),
        encoding="utf-8")
    (out / f"{stem}.html").write_text(
        render_full_html(rows, since_label, intro=intro, section_href_base=base, **kw),
        encoding="utf-8")

    # One page per top-level section (events shown in full on the hub, so no
    # page of their own). Each links back to the hub.
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    plan, _ = _section_plan(rows, career_fair_orgs or [], type_categories,
                            events, internship_keywords)
    for section in plan:
        if section["kind"] == "events":
            continue
        slug = section["slug"]
        (out / f"{stem}_{slug}.md").write_text(
            render_section_markdown(rows, since_label, slug, hub_href=f"{base}.md", **kw),
            encoding="utf-8")
        (out / f"{stem}_{slug}.html").write_text(
            render_section_html(rows, since_label, slug, hub_href=f"{base}.html", **kw),
            encoding="utf-8")

    # Teaser - its "See all" links point straight at the section pages too.
    md_path.write_text(
        render_markdown(rows, since_label, career_fair_orgs, intro, events, social,
                        section_href_base=base, internship_keywords=internship_keywords),
        encoding="utf-8")
    html_path.write_text(
        render_html(rows, since_label, career_fair_orgs, intro, events, social,
                    section_href_base=base, internship_keywords=internship_keywords),
        encoding="utf-8")
    return md_path, html_path
