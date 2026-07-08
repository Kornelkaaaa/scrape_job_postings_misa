"""Devpost hackathon listings (public JSON API).

Devpost is where most student hackathons host their submissions. Their API
lets us request only open/upcoming events, so expired hackathons never even
enter the pipeline.

The stored posted_date is the SUBMISSION DEADLINE (end of the range Devpost
reports) - for a hackathon, "when can I still enter?" matters more than when
it started, and the newsletter's past-event filter then drops it exactly
when entering becomes impossible.

options:
  pages: how many API pages to fetch (default 2, ~18 hackathons per page)
"""
from __future__ import annotations

from datetime import datetime

from ..models import Opportunity

API_URL = "https://devpost.com/api/hackathons"


def _deadline(text: str | None) -> str | None:
    """'May 19 - Aug 17, 2026' -> '2026-08-17' (the part after the dash).

    Single dates ('Aug 17, 2026') have no dash, so split('-')[-1] is the
    whole string - the same code path handles both formats.
    """
    if not text:
        return None
    tail = text.split("-")[-1].strip()
    try:
        return datetime.strptime(tail, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse(source, payload: dict) -> list[Opportunity]:
    opportunities = []
    for hack in payload.get("hackathons", []):
        themes = [t.get("name", "") for t in hack.get("themes", []) if t.get("name")]
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=hack.get("title", ""),
            org=hack.get("organization_name", "") or "Devpost",
            location=(hack.get("displayed_location") or {}).get("location", ""),
            url=hack.get("url", ""),
            posted_date=_deadline(hack.get("submission_period_dates")),
            tags=themes[:4],
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    opportunities = []
    for page in range(1, int(source.options.get("pages", 2)) + 1):
        response = client.get(API_URL, params={
            # status[] appearing twice is how HTTP encodes a list parameter
            "status[]": ["open", "upcoming"],
            "page": page,
        })
        batch = parse(source, response.json())
        opportunities.extend(batch)
        if not batch:
            break  # ran past the last page
    return opportunities
