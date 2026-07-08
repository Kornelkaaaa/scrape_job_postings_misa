"""MISA opportunity pipeline CLI.

    python scraper.py run                       # scrape all enabled sources
    python scraper.py run --type job            # only one opportunity type
    python scraper.py run --source "Stripe"     # only one source
    python scraper.py list-new --since 7d       # show what's new
    python scraper.py newsletter --since 7d     # write Markdown + HTML newsletter
    python scraper.py sources                   # show configured sources
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.config import load_config
from pipeline.db import connect, list_since
from pipeline.models import OPPORTUNITY_TYPES
from pipeline.newsletter import is_career_fair_org, write_newsletter
from pipeline.run import format_summary, run

_SINCE_PATTERN = re.compile(r"^(\d+)([hdw])$")
_SINCE_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=value lines from .env (gitignored) so API keys don't need to be
    set as shell env vars on every run. Real env vars take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def parse_since(value: str) -> str:
    """'7d' / '12h' / '2w' -> ISO timestamp of that long ago (UTC)."""
    match = _SINCE_PATTERN.match(value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(f"invalid --since {value!r}; use e.g. 7d, 12h, 2w")
    amount, unit = int(match.group(1)), match.group(2)
    delta = timedelta(**{_SINCE_UNITS[unit]: amount})
    return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MISA opportunity scraping pipeline")
    parser.add_argument("--config", default="sources.yaml", help="path to sources config")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="scrape all enabled sources and store new items")
    p_run.add_argument("--type", choices=OPPORTUNITY_TYPES, help="only this opportunity type")
    p_run.add_argument("--source", help="only the source with this name")

    p_list = sub.add_parser("list-new", help="list items first seen since a duration ago")
    p_list.add_argument("--since", default="7d", help="e.g. 7d, 12h, 2w (default 7d)")
    p_list.add_argument("--type", choices=OPPORTUNITY_TYPES)
    p_list.add_argument("--json", action="store_true", help="output JSON instead of text")

    p_news = sub.add_parser("newsletter", help="generate Markdown + HTML newsletter")
    p_news.add_argument("--since", default="7d", help="e.g. 7d, 2w (default 7d)")
    p_news.add_argument("--type", choices=OPPORTUNITY_TYPES)
    p_news.add_argument("--out", help="output directory (default from config)")

    sub.add_parser("sources", help="list configured sources")

    # Windows consoles often default to cp1252, which can't print many job
    # titles (Kraków, curly quotes, ...) - never let encoding crash the CLI
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    load_dotenv()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)

    if args.command == "run":
        results = run(config, opportunity_type=args.type, only_source=args.source)
        print(format_summary(results))
        return 1 if results and all(r.error for r in results) else 0

    if args.command == "list-new":
        since_iso = parse_since(args.since)
        conn = connect(config.db_path)
        rows = list_since(conn, since_iso, args.type)
        conn.close()
        if args.json:
            print(json.dumps([dict(r) for r in rows], indent=2))
        else:
            print(f"{len(rows)} new since {args.since} ago:")
            for row in rows:
                loc = f" ({row['location']})" if row["location"] else ""
                star = "* " if is_career_fair_org(row["org"], config.career_fair_orgs) else ""
                print(f"  {star}[{row['opportunity_type']}] {row['title']} - {row['org']}{loc}")
                print(f"      {row['url']}")
        return 0

    if args.command == "newsletter":
        since_iso = parse_since(args.since)
        conn = connect(config.db_path)
        rows = list_since(conn, since_iso, args.type)
        conn.close()
        md_path, html_path = write_newsletter(rows, args.out or config.output_dir,
                                              args.since, config.career_fair_orgs,
                                              config.categories)
        print(f"{len(rows)} items -> {md_path} and {html_path}")
        return 0

    if args.command == "sources":
        for source in config.sources:
            state = "enabled " if source.enabled else "DISABLED"
            print(f"  [{state}] {source.name:<32} type={source.type:<10} "
                  f"opportunity={source.opportunity_type}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
