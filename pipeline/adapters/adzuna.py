"""Adzuna job-search API (free tier). https://developer.adzuna.com/

Adzuna is an AGGREGATOR: it indexes millions of postings from thousands of
boards and employer sites, then lets us search them by keyword/location/
company through an official API. This is our main window into WV-local jobs
(WVU's Taleo site and the state's NEOGOV portal are JavaScript-only).

Credentials come from env vars ADZUNA_APP_ID / ADZUNA_APP_KEY; the source is
skipped with a warning when they're missing, so the rest of the run proceeds.
Keeping secrets in environment variables (loaded from a gitignored .env file)
instead of code is standard practice - code gets committed, secrets must not.

options:
  country: 2-letter code used in the API path (e.g. "us", "gb", "pl")
  what: search phrase (e.g. "business analyst intern")
  where: optional location filter (e.g. "West Virginia")
  company: optional employer filter (must match Adzuna's canonical name -
           "CGI" 400s but "CGI Technologies and Solutions" works)
  max_days_old: optional recency filter
"""
from __future__ import annotations

import hashlib
import logging
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Opportunity, normalize_date

log = logging.getLogger(__name__)

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def _clean_url(url: str) -> str:
    """Drop only utm_* params (utm_source embeds our app_id); the rest of the
    query (se, v) is required for Adzuna's redirect to resolve.

    Lesson learned the hard way: stripping the WHOLE query broke every link
    ("Cannot find page") - always test which params a redirect actually needs.
    """
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def parse(source, payload: dict) -> list[Opportunity]:
    opportunities = []
    for job in payload.get("results", []):
        # Adzuna wraps matched search terms in <strong> - strip the markup
        title = job.get("title", "").replace("<strong>", "").replace("</strong>", "")
        org = (job.get("company") or {}).get("display_name", "")
        location = (job.get("location") or {}).get("display_name", "")
        # Adzuna serves the same posting under multiple ad ids with
        # per-request tracking params, so URL-based dedupe would store the
        # same job many times. Fingerprint the CONTENT instead.
        content = f"{title.lower()}|{org.lower()}|{location.lower()}"
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            org=org,
            location=location,
            url=_clean_url(job.get("redirect_url", "")),
            description=(job.get("description") or "")[:1000],
            posted_date=normalize_date(job.get("created")),
            tags=[(job.get("category") or {}).get("label", "")],
            dedupe_override="adzuna:" + hashlib.sha256(content.encode()).hexdigest(),
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    # os.environ.get returns None when unset - never crash over a missing key
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        log.warning("%s: skipped - set ADZUNA_APP_ID and ADZUNA_APP_KEY env vars", source.name)
        return []  # empty list = "nothing found", the run continues normally

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "content-type": "application/json",
    }
    # Only include params that are actually set - Adzuna 400s on empty ones
    if source.options.get("what"):
        params["what"] = source.options["what"]
    if source.options.get("where"):
        params["where"] = source.options["where"]
    if source.options.get("company"):
        params["company"] = source.options["company"]
    if source.options.get("max_days_old"):
        params["max_days_old"] = source.options["max_days_old"]

    url = API_URL.format(country=source.options.get("country", "us"))
    # params= makes requests build the ?a=b&c=d query string (and URL-encode
    # special characters) for us
    response = client.get(url, params=params)
    return parse(source, response.json())
