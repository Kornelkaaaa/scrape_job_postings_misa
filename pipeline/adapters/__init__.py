"""Adapter registry. Each adapter module exposes the same two functions:

    fetch(source, client) -> list[Opportunity]   # does network I/O
    parse(source, payload) -> list[Opportunity]  # pure, unit-testable

Adding a new source type = one new module here, no core changes.

LEARNING NOTES:
- This dict-of-modules is a simple "plugin registry" - run.py looks up
  ADAPTERS[source.type] instead of a giant if/elif chain. In Python,
  modules are objects you can store in dicts and pass around.
- The fetch/parse split is deliberate: parse() takes data and returns data
  (a "pure function"), so tests can feed it saved fixture files without
  any network. fetch() is the thin networked wrapper around it.
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
