"""Workday CXS job API (used by WVU Medicine and many large employers).

The endpoint is the JSON API behind <host>.myworkdayjobs.com career sites:
POST https://{host}/wday/cxs/{tenant}/{site}/jobs

options:
  host: e.g. wvumedicine.wd1.myworkdayjobs.com
  tenant: e.g. wvumedicine
  site: e.g. WVUH
  search_text: optional query (e.g. "analyst") to keep result sets small
  max_results: pagination cap (default 200)

Workday only reports relative posting ages ("Posted 3 Days Ago"), so
posted_date is left empty; first_seen_at still tracks newness for us.
"""
from __future__ import annotations

from ..models import Opportunity

PAGE_SIZE = 20  # Workday CXS maximum per request


def parse(source, payload: dict) -> list[Opportunity]:
    host = source.options["host"]
    site = source.options["site"]
    opportunities = []
    for job in payload.get("jobPostings", []):
        title = job.get("title", "")
        if not title:
            continue
        path = job.get("externalPath", "")
        opportunities.append(Opportunity(
            opportunity_type=source.opportunity_type,
            source=source.name,
            title=title,
            org=source.org or source.name,
            location=job.get("locationsText", ""),
            url=f"https://{host}/en-US/{site}{path}" if path else "",
        ))
    return opportunities


def fetch(source, client) -> list[Opportunity]:
    host = source.options["host"]
    tenant = source.options["tenant"]
    site = source.options["site"]
    endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    max_results = int(source.options.get("max_results", 200))

    opportunities: list[Opportunity] = []
    offset = 0
    while offset < max_results:
        payload = client.post_json(endpoint, {
            "appliedFacets": {},
            "limit": PAGE_SIZE,
            "offset": offset,
            "searchText": source.options.get("search_text", ""),
        })
        page = parse(source, payload)
        opportunities.extend(page)
        offset += PAGE_SIZE
        if len(page) < PAGE_SIZE or offset >= int(payload.get("total", 0)):
            break
    return opportunities
