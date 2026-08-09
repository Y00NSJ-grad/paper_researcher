from __future__ import annotations

import argparse
import logging

from radar.config import Settings
from radar.runner import RadarRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser("daily", help="Collect the last 48 hours and publish a digest")
    daily.add_argument("--since-hours", type=int, default=48)
    daily.add_argument("--top", type=int, default=10)
    daily.add_argument("--summarize", type=int, default=3)
    daily.add_argument("--limit-per-query", type=int, default=25)
    daily.add_argument("--dry-run", action="store_true")

    weekly = subparsers.add_parser("weekly", help="Collect weekly queries and update trend map")
    weekly.add_argument("--dry-run", action="store_true")
    weekly.add_argument("--skip-collect", action="store_true")

    monthly = subparsers.add_parser("monthly", help="Build a 30-day trend map")
    monthly.add_argument("--days", type=int, default=30)
    monthly.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("init-db", help="Initialize the SQLite database")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, which would expose Slack webhook secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    runner = RadarRunner(Settings.from_env())

    if args.command == "daily":
        path = runner.collect_and_report(
            kind="daily",
            since_hours=args.since_hours,
            top_n=args.top,
            summarize_n=args.summarize,
            dry_run=args.dry_run,
            limit_per_query=args.limit_per_query,
        )
    elif args.command == "weekly":
        if not args.skip_collect:
            runner.collect_and_report(
                kind="weekly",
                since_hours=24 * 8,
                top_n=15,
                summarize_n=3,
                dry_run=args.dry_run,
            )
        path = runner.trend_report("weekly-trends", days=7, dry_run=args.dry_run)
    elif args.command == "monthly":
        path = runner.trend_report("monthly-trends", days=args.days, dry_run=args.dry_run)
    else:
        runner.store.initialize()
        print(f"Initialized {runner.settings.db_path}")
        return
    print(path)


if __name__ == "__main__":
    main()
