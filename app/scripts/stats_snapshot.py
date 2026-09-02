"""Compute (or recompute) daily statistics snapshots by hand.

Usage:
    uv run --env-file .env -m app.scripts.stats_snapshot            # yesterday
    uv run --env-file .env -m app.scripts.stats_snapshot --day 2026-08-31
    uv run --env-file .env -m app.scripts.stats_snapshot --days 30  # last 30 days
    uv run --env-file .env -m app.scripts.stats_snapshot --out /scan_data/mongodump

The maintenance cron does the same thing every night; this is for the initial
history or for re-running a day after a fix. Prediction-quality numbers can be
rebuilt for any past day (they live on titles); editor-usage numbers only reach
back as far as the usage_events TTL.
"""

import argparse
import asyncio
import logging
from datetime import date, timedelta

import certifi
from pymongo import AsyncMongoClient

from app.db.operations.stats_snapshots import store_daily_snapshot, write_snapshot_file
from app.deps import settings_db

logger = logging.getLogger("stats_snapshot")


async def run(days: list[date], out_dir: str | None) -> None:
    kwargs = {"tlsCAFile": certifi.where()} if settings_db.tls_enabled else {}
    client = AsyncMongoClient(settings_db.mongodb_uri, **kwargs)
    db = client.get_database(settings_db.mongodb_db)
    try:
        for day in days:
            doc = await store_daily_snapshot(day, db)
            ov = doc["overview"]
            msg = (
                f"{day}: titles={sum(ov['titles_by_state'].values())} "
                f"sessions={ov['sessions']} editor_time_s={ov['editor_time_s']}"
            )
            if out_dir:
                msg += f" -> {write_snapshot_file(doc, out_dir)}"
            logger.info(msg)
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--day", type=date.fromisoformat, help="single day (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--days", type=int, default=1, help="how many days back to (re)compute"
    )
    parser.add_argument("--out", help="also write <out>/stats/<day>.json files")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.day:
        days = [args.day]
    else:
        yesterday = date.today() - timedelta(days=1)
        days = [yesterday - timedelta(days=i) for i in range(args.days)][::-1]
    asyncio.run(run(days, args.out))


if __name__ == "__main__":
    main()
