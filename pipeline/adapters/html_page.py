"""CSS-selector-driven HTML scraper for official company career pages.

This is classic "web scraping": download the page's HTML and pull data out of
it with CSS selectors (the same selectors used in stylesheets/devtools -
".job-title" means 'elements with class job-title'). BeautifulSoup parses
the HTML into a tree we can query.

Only adapter that scrapes real pages, so it checks robots.txt before fetching.

options:
  selectors:
    item: CSS selector matching one posting card/row      (required)
    title: selector within the item                       (required)
    link: selector for the <a>; href is resolved vs page  (default: item's own <a>)
    org / location / date / tags: optional selectors

Note: pages that render jobs with JavaScript won't work here - requests only
sees the initial HTML, not what a browser builds afterward. Check whether the
site exposes a Greenhouse/Lever/Workday API instead (view page source).
"""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Opportunity, normalize_date


def _text(item, selector: str | None) -> str:
    """Extract the text of the first element matching selector, or ''."""
    if not selector:
        return ""
    node = item.select_one(selector)
    # get_text(strip=True) collapses whitespace and drops inner tags
    return node.get_text(strip=True) if node else ""


def parse(source, payload: str) -> list[Opportunity]:
    selectors = source.options["selectors"]
    soup = BeautifulSoup(payload, "html.parser")

    opportunities = []
    # select() returns ALL matches; each item is one job card we then
    # query WITHIN (so selectors don't leak across cards)
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
            # urljoin resolves relative links: ("https://x.com/careers",
            # "/jobs/1") -> "https://x.com/jobs/1"; absolute hrefs pass through
            url=urljoin(source.url, href) if href else "",
            posted_date=normalize_date(_text(item, selectors.get("date"))),
            tags=[t.strip() for t in tags_text.split(",") if t.strip()],
        ))
    # drop cards where the title selector matched nothing (broken markup)
    return [o for o in opportunities if o.title]


def fetch(source, client) -> list[Opportunity]:
    response = client.get(source.url, check_robots=True)
    return parse(source, response.text)
