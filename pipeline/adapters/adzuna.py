"""Adzuna job-search API (free tier). https://developer.adzuna.com/

Credentials come from env vars ADZUNA_APP_ID / ADZUNA_APP_KEY; the source is
skipped with a warning when they're missing, so the rest of the run proceeds.

options:
  country: 2-letter code used in the API path (e.g. "ca", "us", "gb")
  what: search phrase (e.g. "business analyst intern")
  where: optional location filter
  max_days_old: optional recency filter
"""
from __future__ import annotations

import hashlib
import logging
import os

from ..models import Opportunity, normalize_date

log = logging.getLogger(__name__)

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def parse(source, payload: dict) -> list[Opportunity]:
    opportunities = []
    for job in payload.get("results", []):
        title = job.get("title", "").replace("<strong>", "").replace("</strong>", "")
        org = (job.get("company") or {}).get("display_name", "")
        location = (job.get("location") or {}).get("display_name", "")
        # Adzuna serves the same posting under multiple ad ids with per-request
        # tracking params, so dedupe on content and strip the query (which also
        # carries our app_id) from the stored URL
        content = f"{title.lower()}|{org.lower()}|{location.lower()}"
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            org=org,
            location=location,
            url=job.get("redirect_url", "").split("?")[0],
            description=(job.get("description") or "")[:1000],
            posted_date=normalize_date(job.get("created")),
            tags=[(job.get("category") or {}).get("label", "")],
            dedupe_override="adzuna:" + hashlib.sha256(content.encode()).hexdigest(),
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        log.warning("%s: skipped - set ADZUNA_APP_ID and ADZUNA_APP_KEY env vars", source.name)
        return []

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "content-type": "application/json",
    }
    if source.options.get("what"):  # empty params cause 400s
        params["what"] = source.options["what"]
    if source.options.get("where"):
        params["where"] = source.options["where"]
    if source.options.get("company"):
        params["company"] = source.options["company"]
    if source.options.get("max_days_old"):
        params["max_days_old"] = source.options["max_days_old"]

    url = API_URL.format(country=source.options.get("country", "us"))
    response = client.get(url, params=params)
    return parse(source, response.json())
