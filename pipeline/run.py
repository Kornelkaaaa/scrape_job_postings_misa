"""Orchestration: fetch every enabled source, filter, dedupe-insert, summarize.

LEARNING NOTES:
- This is the "conductor" module: it owns the loop over sources but knows
  nothing about HOW any source is scraped - that lives in the adapters.
  Separating orchestration from implementation is what lets us add a new
  source type without touching this file.
- Error isolation: the try/except around each source means one broken
  website can never crash the whole weekly run. We record the error and
  move on - crucial for anything that runs unattended on a schedule.
- logging vs print: logging has levels (info/warning/error), timestamps,
  and can be redirected; print can't. Libraries should always log.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .adapters import ADAPTERS
from .config import Config
from .db import connect, insert_new
from .filters import filter_relevant
from .http_client import DEFAULT_USER_AGENT, PoliteClient

log = logging.getLogger(__name__)


def _scrub_secrets(text: str) -> str:
    """Error messages can embed request URLs; never let API keys reach logs.

    (HTTP errors include the full URL - which for Adzuna contains
    app_id/app_key as query params. CI logs are public!)
    """
    return re.sub(r"\b(app_id|app_key|api_key|apikey|key|token)=[^&\s]+",
                  r"\1=***", text, flags=re.I)


@dataclass
class SourceResult:
    """Per-source stats for the end-of-run summary table."""
    source: str
    fetched: int = 0    # how many postings the site returned
    relevant: int = 0   # how many survived keyword/location filters
    new: int = 0        # how many weren't already in the database
    error: str | None = None


def run(config: Config, opportunity_type: str | None = None,
        only_source: str | None = None) -> list[SourceResult]:
    # One shared HTTP client for the whole run - so the polite delay applies
    # across sources too, and connections get reused.
    client = PoliteClient(
        user_agent=config.user_agent or DEFAULT_USER_AGENT,
        delay_seconds=config.delay_seconds,
    )
    conn = connect(config.db_path)
    results = []

    # try/finally guarantees the DB connection closes even if we crash
    try:
        for source in config.sources:
            # --- skip filters (CLI flags / config switches) ---
            if not source.enabled:
                continue
            if opportunity_type and source.opportunity_type != opportunity_type:
                continue
            if only_source and source.name != only_source:
                continue

            result = SourceResult(source=source.name)
            results.append(result)

            # Look up the adapter module by its type string - the registry
            # dict in adapters/__init__.py maps "greenhouse" -> module etc.
            adapter = ADAPTERS.get(source.type)
            if adapter is None:
                result.error = f"unknown source type {source.type!r}"
                log.error("%s: %s", source.name, result.error)
                continue

            # one broken source must never take down the whole run
            try:
                fetched = adapter.fetch(source, client)
            except Exception as exc:
                # type(exc).__name__ gives "HTTPError"/"Timeout" etc. -
                # more useful in a summary than just the message
                result.error = _scrub_secrets(f"{type(exc).__name__}: {exc}")
                log.error("%s: fetch failed - %s", source.name, result.error)
                continue

            result.fetched = len(fetched)
            relevant = filter_relevant(fetched, config, source)
            result.relevant = len(relevant)
            result.new = insert_new(conn, relevant)
            log.info("%s: %d fetched, %d relevant, %d new",
                     source.name, result.fetched, result.relevant, result.new)
    finally:
        conn.close()

    return results


def format_summary(results: list[SourceResult]) -> str:
    """Render the results as an aligned text table.

    f-string alignment: {x:<32} pads to 32 chars left-aligned,
    {x:>8} right-aligns in 8 - that's all a text table is.
    """
    lines = ["", f"{'source':<32} {'fetched':>8} {'relevant':>9} {'new':>5}  status"]
    lines.append("-" * 70)
    for r in results:
        status = f"ERROR: {r.error}" if r.error else "ok"
        lines.append(f"{r.source:<32} {r.fetched:>8} {r.relevant:>9} {r.new:>5}  {status}")
    total_new = sum(r.new for r in results)
    failures = sum(1 for r in results if r.error)
    lines.append("-" * 70)
    lines.append(f"total new: {total_new}   sources failed: {failures}/{len(results)}")
    return "\n".join(lines)
