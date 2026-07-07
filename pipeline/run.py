"""Orchestration: fetch every enabled source, filter, dedupe-insert, summarize."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .adapters import ADAPTERS
from .config import Config
from .db import connect, insert_new
from .filters import filter_relevant
from .http_client import DEFAULT_USER_AGENT, PoliteClient

log = logging.getLogger(__name__)


@dataclass
class SourceResult:
    source: str
    fetched: int = 0
    relevant: int = 0
    new: int = 0
    error: str | None = None


def run(config: Config, opportunity_type: str | None = None,
        only_source: str | None = None) -> list[SourceResult]:
    client = PoliteClient(
        user_agent=config.user_agent or DEFAULT_USER_AGENT,
        delay_seconds=config.delay_seconds,
    )
    conn = connect(config.db_path)
    results = []

    try:
        for source in config.sources:
            if not source.enabled:
                continue
            if opportunity_type and source.opportunity_type != opportunity_type:
                continue
            if only_source and source.name != only_source:
                continue

            result = SourceResult(source=source.name)
            results.append(result)

            adapter = ADAPTERS.get(source.type)
            if adapter is None:
                result.error = f"unknown source type {source.type!r}"
                log.error("%s: %s", source.name, result.error)
                continue

            # one broken source must never take down the whole run
            try:
                fetched = adapter.fetch(source, client)
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"
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
