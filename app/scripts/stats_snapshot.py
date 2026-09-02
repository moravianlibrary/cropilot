"""Recompute the stored "latest statistics" document by hand.

Usage:
    uv run --env-file .env -m app.scripts.stats_snapshot
    uv run --env-file .env -m app.scripts.stats_snapshot --days 90
    uv run --env-file .env -m app.scripts.stats_snapshot --out /scan_data/mongodump

The maintenance cron does the same every night (trailing 30-day window); this
is for a first fill right after deploying or for checking the numbers.
"""

import argparse
import asyncio
import logging

import certifi
from pymongo import AsyncMongoClient

from app.db.operations.stats_snapshots import (
    SNAPSHOT_WINDOW_DAYS,
    store_snapshot,
    write_snapshot_file,
)
from app.deps import settings_db

logger = logging.getLogger("stats_snapshot")


async def run(days: int, out_dir: str | None) -> None:
    kwargs = {"tlsCAFile": certifi.where()} if settings_db.tls_enabled else {}
    client = AsyncMongoClient(settings_db.mongodb_uri, **kwargs)
    db = client.get_database(settings_db.mongodb_db)
    try:
        doc = await store_snapshot(db, days)
        ov = doc["overview"]
        msg = (
            f"window={days}d titles={sum(ov['titles_by_state'].values())} "
            f"scans={ov['scans']['total']} sessions={ov['sessions']} "
            f"editor_time_s={ov['editor_time_s']}"
        )
        if out_dir:
            msg += f" -> {write_snapshot_file(doc, out_dir)}"
        logger.info(msg)
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=SNAPSHOT_WINDOW_DAYS, help="trailing window length"
    )
    parser.add_argument("--out", help="also write <out>/stats/latest.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run(args.days, args.out))


if __name__ == "__main__":
    main()
