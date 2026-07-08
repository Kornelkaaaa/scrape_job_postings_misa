"""Render new opportunities into a Markdown + HTML newsletter.

Files-only delivery: paste the HTML into Mailchimp/Substack/Gmail, or share
the Markdown directly (Slack/Discord/Notion).
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import date
from pathlib import Path

TYPE_HEADINGS = {
    "job": "💼 Jobs & Internships",
    "hackathon": "🚀 Hackathons",
    "conference": "🎤 Conferences & Events",
    "other": "✨ Other Opportunities",
}
CAREER_FAIR_HEADING = "🎓 WVU Career Fair Employers"


def is_career_fair_org(org: str, career_fair_orgs: list[str]) -> bool:
    """Case-insensitive containment either way, so a config entry 'Deloitte'
    matches org 'Deloitte Consulting LLP' and 'Leidos Inc.' matches 'Leidos'."""
    org_l = org.lower().strip()
    if not org_l:
        return False
    return any(
        name.lower() in org_l or org_l in name.lower()
        for name in career_fair_orgs if name.strip()
    )


def _partition(rows: list[sqlite3.Row], career_fair_orgs: list[str]):
    fair = [r for r in rows if is_career_fair_org(r["org"], career_fair_orgs)]
    rest = [r for r in rows if not is_career_fair_org(r["org"], career_fair_orgs)]
    return fair, rest


def _group_by_type(rows: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(row["opportunity_type"], []).append(row)
    return groups


def _item_meta(row: sqlite3.Row) -> str:
    parts = [p for p in [row["org"], row["location"], row["posted_date"]] if p]
    tags = json.loads(row["tags"] or "[]")
    if tags:
        parts.append(", ".join(tags[:4]))
    return " · ".join(parts)


def render_markdown(rows: list[sqlite3.Row], since_label: str,
                    career_fair_orgs: list[str] | None = None) -> str:
    today = date.today().isoformat()
    fair, rest = _partition(rows, career_fair_orgs or [])
    lines = [
        f"# MISA Opportunities Newsletter — {today}",
        "",
        f"*{len(rows)} new opportunities found in the last {since_label}.*",
        "",
    ]
    sections = ([(CAREER_FAIR_HEADING, fair)] if fair else []) + [
        (TYPE_HEADINGS.get(t, t.title()), items)
        for t, items in _group_by_type(rest).items()
    ]
    for heading, items in sections:
        lines.append(f"## {heading}")
        lines.append("")
        for row in items:
            lines.append(f"- **[{row['title']}]({row['url']})**")
            meta = _item_meta(row)
            if meta:
                lines.append(f"  {meta}")
        lines.append("")
    lines.append("---")
    lines.append("*Generated automatically by the MISA opportunity pipeline.*")
    return "\n".join(lines)


def _html_section(heading: str, items: list[sqlite3.Row], accent: str = "#1d4ed8") -> str:
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
    return (
        f'<h2 style="font-size:18px;color:#111827;margin:28px 0 4px;">{html.escape(heading)}</h2>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f'{"".join(cards)}</table>'
    )


def render_html(rows: list[sqlite3.Row], since_label: str,
                career_fair_orgs: list[str] | None = None) -> str:
    today = date.today().isoformat()
    fair, rest = _partition(rows, career_fair_orgs or [])
    sections = []
    if fair:  # WVU gold accent for employers members can meet in person
        sections.append(_html_section(CAREER_FAIR_HEADING, fair, accent="#b45309"))
    for opp_type, items in _group_by_type(rest).items():
        sections.append(_html_section(TYPE_HEADINGS.get(opp_type, opp_type.title()), items))

    # table-based, inline-styled layout for email-client compatibility
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>MISA Opportunities — {today}</title></head>
<body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:8px;padding:32px;">
<tr><td>
<h1 style="font-size:22px;color:#111827;margin:0 0 4px;">MISA Opportunities Newsletter</h1>
<p style="font-size:14px;color:#6b7280;margin:0;">{today} — {len(rows)} new opportunities in the last {since_label}</p>
{"".join(sections)}
<p style="font-size:12px;color:#9ca3af;margin-top:32px;">Generated automatically by the MISA opportunity pipeline.</p>
</td></tr></table>
</td></tr></table>
</body></html>"""


def write_newsletter(rows: list[sqlite3.Row], output_dir: str | Path,
                     since_label: str,
                     career_fair_orgs: list[str] | None = None) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    md_path = out / f"newsletter_{stamp}.md"
    html_path = out / f"newsletter_{stamp}.html"
    md_path.write_text(render_markdown(rows, since_label, career_fair_orgs), encoding="utf-8")
    html_path.write_text(render_html(rows, since_label, career_fair_orgs), encoding="utf-8")
    return md_path, html_path
