"""Render new opportunities into a Markdown + HTML newsletter.

Files-only delivery: paste the HTML into Mailchimp/Substack/Gmail, or share
the Markdown directly (Slack/Discord/Notion).

LEARNING NOTES:
- Email HTML is stuck in ~2003: no external CSS, no flexbox - clients like
  Outlook strip <style> blocks. That's why the HTML here uses <table> layout
  and repeats style="..." inline on every element. Ugly but bulletproof.
- html.escape(): job titles come from the internet; one "<script>" or a
  stray "<" in a title would break (or attack) the page. ALWAYS escape
  external text before putting it into HTML.
- Separating render_markdown/render_html from write_newsletter keeps the
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

# Section headings per opportunity type - the schema supports hackathons and
# conferences already, so the newsletter grows new sections automatically
# when Phase 2 sources are enabled.
TYPE_HEADINGS = {
    "job": "💼 Jobs & Internships",
    "hackathon": "🚀 Hackathons",
    "conference": "🎤 Conferences & Events",
    "other": "✨ Other Opportunities",
}
# URL-safe anchor ids for the jump-to navigation (emoji don't belong in URLs)
TYPE_SLUGS = {"job": "jobs", "hackathon": "hackathons",
              "conference": "conferences", "other": "other"}
CAREER_FAIR_HEADING = "🎓 WVU Career Fair Employers"
INTERNSHIPS_HEADING = "🎯 Internships & Co-ops"


def _is_internship(row: sqlite3.Row, keywords: list[str]) -> bool:
    haystack = f"{row['title']} {' '.join(json.loads(row['tags'] or '[]'))}"
    return any(re.search(rf"\b{re.escape(k)}\b", haystack, re.I) for k in keywords)

# opportunity types whose date means "when it happens" (vs "when posted") -
# these expire and must not be advertised after the fact
EVENT_TYPES = ("hackathon", "conference")

# Minimum days of runway an event needs to be worth advertising. A hackathon
# whose submission deadline is 4 days out isn't realistically joinable by
# newsletter readers; a conference can still be attended tomorrow.
MIN_LEAD_DAYS = {"hackathon": 5, "conference": 0}


def _drop_past_events(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Never advertise an event that's over - or too close to join.

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


def _make_section(slug: str, heading: str, items: list[sqlite3.Row], cats: dict) -> dict:
    subs = []
    if cats:
        for i, (name, cat_items) in enumerate(_group_by_category(items, cats).items()):
            subs.append({"slug": f"{slug}-{i}", "heading": name, "items": cat_items})
    return {"slug": slug, "heading": heading, "items": items, "subs": subs}


def _section_plan(rows: list[sqlite3.Row], career_fair_orgs: list[str],
                  type_categories: dict,
                  internship_keywords: list[str] | None = None) -> tuple[list[dict], int]:
    """Shared outline both renderers (and the nav) walk: a list of
    {slug, heading, items, subs} dicts. Computing this ONCE guarantees the
    navigation links and the actual sections can never disagree."""
    rows = _drop_past_events(rows)
    fair, rest = _partition(rows, career_fair_orgs)
    plan = []
    if fair:
        plan.append({"slug": "career-fair", "heading": CAREER_FAIR_HEADING,
                     "items": fair, "subs": []})
    for opp_type, items in _group_by_type(rest).items():
        slug = TYPE_SLUGS.get(opp_type, opp_type)
        cats = type_categories.get(opp_type) or {}
        heading = TYPE_HEADINGS.get(opp_type, opp_type.title())
        # internships get their own section ABOVE the remaining jobs -
        # they're the postings members care about most. Kept flat (no
        # category subsections): a couple dozen internships don't need them.
        if opp_type == "job" and internship_keywords:
            internships = [r for r in items if _is_internship(r, internship_keywords)]
            if internships:
                plan.append(_make_section("internships", INTERNSHIPS_HEADING,
                                          internships, {}))
                items = [r for r in items if not _is_internship(r, internship_keywords)]
                heading = "💼 Jobs"  # no longer "& Internships" - they moved up
        plan.append(_make_section(slug, heading, items, cats))
    return plan, len(rows)


def _item_meta(row: sqlite3.Row) -> str:
    """The grey info line under a title: 'Org · Location · Date · tags'."""
    parts = [p for p in [row["org"], row["location"], row["posted_date"]] if p]
    tags = json.loads(row["tags"] or "[]")  # stored as JSON text in SQLite
    if tags:
        parts.append(", ".join(tags[:4]))   # at most 4 tags, keep it short
    return " · ".join(parts)


def _md_items(row_list: list[sqlite3.Row]) -> list[str]:
    """Markdown bullet lines for a list of rows."""
    lines = []
    for row in row_list:
        # [text](url) is a Markdown link; ** makes it bold
        lines.append(f"- **[{row['title']}]({row['url']})**")
        meta = _item_meta(row)
        if meta:
            lines.append(f"  {meta}")  # two-space indent keeps it in the bullet
    return lines


def render_markdown(rows: list[sqlite3.Row], since_label: str,
                    career_fair_orgs: list[str] | None = None,
                    categories: dict | None = None,
                    hackathon_categories: dict | None = None,
                    internship_keywords: list[str] | None = None) -> str:
    today = date.today().isoformat()
    # each opportunity type can have its own category scheme
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    plan, total = _section_plan(rows, career_fair_orgs or [], type_categories,
                                internship_keywords)
    lines = [
        f"# MISA Opportunities Newsletter — {today}",
        "",
        f"*{total} new opportunities found in the last {since_label}.*",
        "",
    ]
    # Jump-to navigation. Raw <a id=...> anchors work on GitHub and in
    # VS Code; markdown heading auto-anchors vary per renderer, explicit
    # ids don't.
    if plan:
        nav = " · ".join(f"[{s['heading']} ({len(s['items'])})](#{s['slug']})" for s in plan)
        lines += [f"**Jump to:** {nav}", ""]
    for section in plan:
        lines += [f'<a id="{section["slug"]}"></a>', "", f"## {section['heading']}", ""]
        if section["subs"]:
            # per-section mini-nav so 100+ jobs are one click, not a scroll
            sub_nav = " · ".join(f"[{s['heading']} ({len(s['items'])})](#{s['slug']})"
                                 for s in section["subs"])
            lines += [sub_nav, ""]
            for sub in section["subs"]:
                lines += [f'<a id="{sub["slug"]}"></a>', "",
                          f"### {sub['heading']}", ""] + _md_items(sub["items"]) + [""]
        else:
            lines += _md_items(section["items"]) + [""]
    lines.append("---")
    lines.append("*Generated automatically by the MISA opportunity pipeline.*")
    return "\n".join(lines)


def _html_cards(items: list[sqlite3.Row], accent: str = "#1d4ed8") -> str:
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


def _html_section(section: dict, accent: str = "#1d4ed8") -> str:
    """One section from the plan: anchored h2, optional anchored h3 subs."""
    parts = [f'<h2 id="{section["slug"]}" '
             f'style="font-size:18px;color:#111827;margin:28px 0 4px;">'
             f'{html.escape(section["heading"])}</h2>']
    if section["subs"]:
        for sub in section["subs"]:
            parts.append(f'<h3 id="{sub["slug"]}" '
                         f'style="font-size:15px;color:#374151;margin:18px 0 2px;">'
                         f'{html.escape(sub["heading"])}</h3>')
            parts.append(_html_cards(sub["items"], accent))
    else:
        parts.append(_html_cards(section["items"], accent))
    return "".join(parts)


def _html_nav(plan: list[dict]) -> str:
    """Jump-to panel under the header: one line per section (bold link with
    count), followed by its category links. Anchor jumps work in most
    desktop/webmail clients; where unsupported they render as plain text."""
    link = ('<a href="#{slug}" style="color:#1d4ed8;text-decoration:none;'
            'font-size:13px;{extra}">{label}</a>')
    rows_html = []
    for section in plan:
        parts = [link.format(slug=section["slug"], extra="font-weight:600;",
                             label=f'{html.escape(section["heading"])} ({len(section["items"])})')]
        parts += [link.format(slug=sub["slug"], extra="",
                              label=f'{html.escape(sub["heading"])} ({len(sub["items"])})')
                  for sub in section["subs"]]
        rows_html.append('<div style="margin:3px 0;">' + " &nbsp;·&nbsp; ".join(parts) + "</div>")
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            '<tr><td style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;'
            'padding:12px 16px;margin-top:16px;">'
            '<div style="font-size:12px;color:#6b7280;text-transform:uppercase;'
            'letter-spacing:0.05em;margin-bottom:6px;">Jump to</div>'
            + "".join(rows_html) + "</td></tr></table>")


def render_html(rows: list[sqlite3.Row], since_label: str,
                career_fair_orgs: list[str] | None = None,
                categories: dict | None = None,
                hackathon_categories: dict | None = None,
                internship_keywords: list[str] | None = None) -> str:
    today = date.today().isoformat()
    type_categories = {"job": categories or {}, "hackathon": hackathon_categories or {}}
    plan, total = _section_plan(rows, career_fair_orgs or [], type_categories,
                                internship_keywords)
    rows_count = total
    sections = []
    if plan:
        sections.append(_html_nav(plan))
    for section in plan:
        # WVU gold accent for employers members can meet in person
        accent = "#b45309" if section["slug"] == "career-fair" else "#1d4ed8"
        sections.append(_html_section(section, accent))

    # 600px centered white card on grey - the classic email layout that
    # survives every mail client
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>MISA Opportunities — {today}</title></head>
<body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:8px;padding:32px;">
<tr><td>
<h1 style="font-size:22px;color:#111827;margin:0 0 4px;">MISA Opportunities Newsletter</h1>
<p style="font-size:14px;color:#6b7280;margin:0 0 16px;">{today} — {rows_count} new opportunities in the last {since_label}</p>
{"".join(sections)}
<p style="font-size:12px;color:#9ca3af;margin-top:32px;">Generated automatically by the MISA opportunity pipeline.</p>
</td></tr></table>
</td></tr></table>
</body></html>"""


def write_newsletter(rows: list[sqlite3.Row], output_dir: str | Path,
                     since_label: str,
                     career_fair_orgs: list[str] | None = None,
                     categories: dict | None = None,
                     hackathon_categories: dict | None = None,
                     internship_keywords: list[str] | None = None) -> tuple[Path, Path]:
    """Render both formats and write date-stamped files; returns their paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    md_path = out / f"newsletter_{stamp}.md"       # pathlib's / joins paths
    html_path = out / f"newsletter_{stamp}.html"
    md_path.write_text(render_markdown(rows, since_label, career_fair_orgs,
                                       categories, hackathon_categories,
                                       internship_keywords),
                       encoding="utf-8")
    html_path.write_text(render_html(rows, since_label, career_fair_orgs,
                                     categories, hackathon_categories,
                                     internship_keywords),
                         encoding="utf-8")
    return md_path, html_path
