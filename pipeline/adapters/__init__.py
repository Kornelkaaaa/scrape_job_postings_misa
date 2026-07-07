"""Adapter registry. Each adapter module exposes:

    fetch(source, client) -> list[Opportunity]   # does network I/O
    parse(source, payload) -> list[Opportunity]  # pure, unit-testable

Adding a new source type = one new module here, no core changes.
"""
from . import adzuna, greenhouse, html_page, json_api, lever, rss, usajobs, workday

ADAPTERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "rss": rss,
    "json_api": json_api,
    "html": html_page,
    "adzuna": adzuna,
    "usajobs": usajobs,
    "workday": workday,
}
