import logging
import os

import certifi
from hatchet_sdk import (
    Context,
    EmptyModel,
)
from pymongo import AsyncMongoClient

from app.db.operations.stats_snapshots import store_snapshot, write_snapshot_file
from app.db.operations.work_stats import rollup_work_stats
from app.deps import settings_db
from app.tasks.hatchet_client import hatchet

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


def _async_client() -> AsyncMongoClient:
    kwargs = {"tlsCAFile": certifi.where()} if settings_db.tls_enabled else {}
    return AsyncMongoClient(settings_db.mongodb_uri, **kwargs)


@maintenance_task.task()
async def work_stats_rollup(input: EmptyModel, ctx: Context) -> dict[str, str]:
    """Folds the editor usage events of the day into ``Title.work_stats``.

    Keeps the human time per title after the raw events expire; the time
    statistics (``/stats/time``) read it from the titles.
    """
    ctx.log("Rolling up editor usage events into work_stats.")
    client = _async_client()
    try:
        result = await rollup_work_stats(client.get_database(settings_db.mongodb_db))
    except Exception as e:  # noqa: BLE001 - the snapshot (child task) must still run
        logger.exception("work_stats rollup failed")
        ctx.log(f"work_stats rollup failed: {e}")
        return {"status": "failed", "error": str(e)}
    finally:
        await client.close()
    ctx.log(f"work_stats updated on {result['titles']} titles.")
    return {"status": "success", "titles": str(result["titles"])}


@maintenance_task.task(parents=[work_stats_rollup])
async def stats_snapshot(input: EmptyModel, ctx: Context) -> dict[str, str]:
    """Refreshes the stored "latest statistics" so they outlive the events TTL.

    Runs in the same nightly cron as the dump and overwrites a single document
    (``stats_snapshots`` / ``latest``, which mongodump then includes) plus
    ``<dump location>/stats/latest.json``. Independent of the dump task so a
    failed dump does not lose the statistics; runs after the work_stats
    roll-up so the time statistics are current.
    """
    ctx.log("Computing latest statistics snapshot.")

    client = _async_client()
    try:
        db = client.get_database(settings_db.mongodb_db)
        doc = await store_snapshot(db)
    finally:
        await client.close()

    path = write_snapshot_file(doc, os.path.join(SCANS_VOLUME_PATH, "mongodump"))
    ctx.log(f"Latest statistics stored ({path}).")

    return {
        "status": "success",
        "computed_at": doc["computed_at"].isoformat(),
        "file": path,
    }
