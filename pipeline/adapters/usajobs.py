"""USAJOBS search API (official, free). https://developer.usajobs.gov/

Strong coverage for West Virginia: FBI CJIS (Clarksburg), NIOSH (Morgantown),
and other federal employers hire analysts and students there.

Credentials come from env vars USAJOBS_API_KEY and USAJOBS_EMAIL (both issued
at signup); the source is skipped with a warning when missing.

Unlike Adzuna (which takes credentials as URL params), USAJOBS wants them in
HTTP *headers* - every API has its own authentication style, and the docs
tell you which. Header auth is slightly nicer: headers don't end up in logs.

options:
  keyword: search phrase (e.g. "analyst")
  location_name: e.g. "West Virginia"
"""
from __future__ import annotations

import logging
import os

from ..models import Opportunity, normalize_date

log = logging.getLogger(__name__)

API_URL = "https://data.usajobs.gov/api/search"


def parse(source, payload: dict) -> list[Opportunity]:
    # Government APIs love nesting: the real job data lives at
    # SearchResult -> SearchResultItems[] -> MatchedObjectDescriptor
    items = (payload.get("SearchResult") or {}).get("SearchResultItems", [])
    opportunities = []
    for item in items:
        job = item.get("MatchedObjectDescriptor", {})
        categories = [c.get("Name", "") for c in job.get("JobCategory", []) if c.get("Name")]
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=job.get("PositionTitle", ""),
            org=job.get("OrganizationName", ""),
            location=job.get("PositionLocationDisplay", ""),
            url=job.get("PositionURI", ""),
            # chained (x or {}) guards three levels of maybe-missing nesting
            description=((job.get("UserArea") or {}).get("Details") or {}).get(
                "JobSummary", "")[:1000],
            posted_date=normalize_date(job.get("PublicationStartDate")),
            tags=categories,
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    api_key = os.environ.get("USAJOBS_API_KEY")
    email = os.environ.get("USAJOBS_EMAIL")
    if not api_key or not email:
        log.warning("%s: skipped - set USAJOBS_API_KEY and USAJOBS_EMAIL env vars", source.name)
        return []

    params = {
        "Keyword": source.options.get("keyword", ""),
        "LocationName": source.options.get("location_name", ""),
        "ResultsPerPage": 100,
    }
    # USAJOBS quirk: the API key goes in Authorization-Key, and your
    # registered email must be sent as the User-Agent
    headers = {"Authorization-Key": api_key, "User-Agent": email}
    response = client.get(API_URL, params=params, headers=headers)
    return parse(source, response.json())
