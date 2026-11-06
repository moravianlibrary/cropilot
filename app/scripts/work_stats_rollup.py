"""Fold editor usage events into ``Title.work_stats`` by hand.

Usage:
    uv run --env-file .env -m app.scripts.work_stats_rollup

The maintenance cron does the same every night before the statistics
snapshot, and its first run folds in every event still within the TTL, so
nothing has to be run after a deploy. This is for getting the numbers
sooner or for checking them.
"""

import asyncio
import logging

import certifi
from pymongo import AsyncMongoClient

from app.db.operations.work_stats import rollup_work_stats
from app.deps import settings_db

logger = logging.getLogger("work_stats_rollup")


async def run() -> None:
    kwargs = {"tlsCAFile": certifi.where()} if settings_db.tls_enabled else {}
    client = AsyncMongoClient(settings_db.mongodb_uri, **kwargs)
    db = client.get_database(settings_db.mongodb_db)
    try:
        result = await rollup_work_stats(db)
        logger.info(
            f"titles={result['titles']} events_from={result['events_from']} "
            f"events_until={result['events_until']}"
        )
    finally:
        await client.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
