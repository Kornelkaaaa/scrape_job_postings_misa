"""Major League Hacking season events page.

MLH's page uses Tailwind CSS (auto-generated class names that change between
deploys - useless as scraping selectors). But each event card carries
schema.org MICRODATA: <meta itemprop="startDate" content="2026-09-11..."> etc.
Sites add this markup for Google's event search, and it's far more stable
than any CSS class - always check for it before writing fragile selectors.

Config url points at a season page, e.g. https://mlh.io/seasons/2027/events
(bump the season in sources.yaml each summer).
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import Opportunity, normalize_date


def parse(source, payload: str) -> list[Opportunity]:
    soup = BeautifulSoup(payload, "html.parser")
    opportunities = []
    # Every event card is an anchor tracking-linked back to MLH
    for card in soup.select('a[href*="utm_source=mlh"]'):
        # collect the card's microdata into a simple dict
        meta = {m.get("itemprop"): m.get("content", "") for m in card.find_all("meta")}
        title_node = card.find("h4")
        title = title_node.get_text(strip=True) if title_node else ""
        if not title:
            continue

        online = "Online" in meta.get("eventAttendanceMode", "")
        if online:
            location = "Digital"
        else:
            parts = [meta.get(k, "") for k in ("addressLocality", "addressRegion", "addressCountry")]
            location = ", ".join(p for p in parts if p)

        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            org="MLH",
            location=location,
            # microdata url is the event's own site, without tracking params -
            # a stable dedupe key across weekly scrapes
            url=meta.get("url", "") or card.get("href", "").split("?")[0],
            posted_date=normalize_date(meta.get("startDate")),
            tags=["Digital" if online else "In-Person"],
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url)
    return parse(source, response.text)
