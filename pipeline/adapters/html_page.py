"""CSS-selector-driven HTML scraper for official company career pages.

Only adapter that scrapes real pages, so it checks robots.txt before fetching.

options:
  selectors:
    item: CSS selector matching one posting card/row      (required)
    title: selector within the item                       (required)
    link: selector for the <a>; href is resolved vs page  (default: title's or item's own <a>)
    org / location / date / tags: optional selectors

Note: pages that render jobs with JavaScript won't work here — check whether
the site exposes a Greenhouse/Lever/Workday API instead (view page source).
"""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Opportunity, normalize_date


def _text(item, selector: str | None) -> str:
    if not selector:
        return ""
    node = item.select_one(selector)
    return node.get_text(strip=True) if node else ""


def parse(source, payload: str) -> list[Opportunity]:
    selectors = source.options["selectors"]
    soup = BeautifulSoup(payload, "html.parser")

    opportunities = []
    for item in soup.select(selectors["item"]):
        link_node = item.select_one(selectors.get("link") or "a[href]")
        href = link_node.get("href", "") if link_node else ""
        tags_text = _text(item, selectors.get("tags"))
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=_text(item, selectors["title"]),
            org=_text(item, selectors.get("org")) or source.org or source.name,
            location=_text(item, selectors.get("location")),
            url=urljoin(source.url, href) if href else "",
            posted_date=normalize_date(_text(item, selectors.get("date"))),
            tags=[t.strip() for t in tags_text.split(",") if t.strip()],
        ))
    return [o for o in opportunities if o.title]


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url, check_robots=True)
    return parse(source, response.text)
