import logging
import os
from datetime import date, timedelta

import certifi
from pymongo import AsyncMongoClient

from app.db.operations.stats_snapshots import store_daily_snapshot, write_snapshot_file
from app.deps import settings_db
from app.tasks.hatchet_client import hatchet


from hatchet_sdk import (
    Context,
    EmptyModel,
)


logger = logging.getLogger(__name__)
maintenance_task = hatchet.workflow(
    name="maintenance", on_crons=["0 2 * * *"]
)  # Runs once 2 am daily

SCANS_VOLUME_PATH = os.getenv("SCANS_VOLUME_PATH")
MONGODB_URI = os.getenv("MONGODB_URI")


@maintenance_task.task()
def mongodump(input: EmptyModel, ctx: Context) -> dict[str, str]:
    """Performs a MongoDB dump for maintenance purposes."""
    ctx.log("Starting MongoDB dump.")

    location = os.path.join(SCANS_VOLUME_PATH, "mongodump")

    ret = os.system(f"mongodump --uri={MONGODB_URI} --out={location} --gzip")
    if ret != 0:
        msg = f"mongodump exited with code {ret}"
        logger.error(msg)
        ctx.log(msg)
        raise RuntimeError(msg)

    ctx.log("Mongodump completed successfully.")

    return {
        "status": "success",
        "location": location,
    }


@maintenance_task.task()
async def stats_snapshot(input: EmptyModel, ctx: Context) -> dict[str, str]:
    """Stores yesterday's statistics aggregates so they outlive the events TTL.

    Runs in the same nightly cron as the dump: one document per day in the
    ``stats_snapshots`` collection (which mongodump then includes) plus a JSON
    copy at ``<dump location>/stats/<day>.json``. Independent of the dump task
    so a failed dump does not lose the day's statistics.
    """
    day = date.today() - timedelta(days=1)
    ctx.log(f"Computing statistics snapshot for {day}.")

    kwargs = {"tlsCAFile": certifi.where()} if settings_db.tls_enabled else {}
    client = AsyncMongoClient(settings_db.mongodb_uri, **kwargs)
    try:
        db = client.get_database(settings_db.mongodb_db)
        doc = await store_daily_snapshot(day, db)
    finally:
        await client.close()

    path = write_snapshot_file(doc, os.path.join(SCANS_VOLUME_PATH, "mongodump"))
    ctx.log(f"Statistics snapshot for {day} stored ({path}).")

    return {"status": "success", "day": day.isoformat(), "file": path}
