"""Tech conferences from confs.tech (community-maintained, open data).

The confs.tech site is backed by plain JSON files in a public GitHub repo,
organized by year and topic: conferences/{year}/{topic}.json. Reading data
straight from a Git repo like this is a common pattern for community datasets
- no API keys, no rate limits, and the raw.githubusercontent.com URLs serve
the files as-is.

options:
  topics: list of topic files to read (default: data, security, devops, general)
  years: list of years (default: current + next, since conferences are
         announced well ahead)
"""
from __future__ import annotations

import logging
from datetime import date

import requests

from ..models import Opportunity, normalize_date

log = logging.getLogger(__name__)

URL = ("https://raw.githubusercontent.com/tech-conferences/conference-data/"
       "main/conferences/{year}/{topic}.json")
DEFAULT_TOPICS = ["data", "security", "devops", "general"]


def parse(source, payload: list, topic: str = "") -> list[Opportunity]:
    opportunities = []
    for conf in payload:
        title = conf.get("name", "")
        if not title:
            continue
        # Hybrid events carry online=true AND a city - keep the city so the
        # location filter can drop far-away ones (a "Berlin (hybrid)" conf is
        # really a German event, not an online one). "Online" only when
        # there's no venue at all.
        location = ", ".join(p for p in (conf.get("city"), conf.get("country")) if p)
        if not location and conf.get("online"):
            location = "Online"
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            org=source.org or "confs.tech",
            location=location,
            url=conf.get("url", ""),
            posted_date=normalize_date(conf.get("startDate")),
            tags=[topic] if topic else [],
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    topics = source.options.get("topics", DEFAULT_TOPICS)
    this_year = date.today().year
    years = source.options.get("years", [this_year, this_year + 1])

    opportunities = []
    for year in years:
        for topic in topics:
            try:
                response = client.get(URL.format(year=year, topic=topic))
            except requests.HTTPError:
                # next year's file may not exist yet - skip, don't fail the source
                log.debug("%s: no data for %s/%s", source.name, year, topic)
                continue
            opportunities.extend(parse(source, response.json(), topic))
    # the same conference can appear in several topic files; URL-based
    # dedupe in the DB collapses them, so duplicates here are harmless
    return opportunities
