"""Daily snapshots of the admin statistics.

Raw usage events expire (TTL) and the nightly mongodump overwrites itself, so
the aggregates would eventually be lost. The maintenance cron therefore stores
one snapshot per day in ``stats_snapshots`` (no TTL, included in the dump) and
writes the same document as a JSON file next to the dump.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta

from bson import ObjectId
from fastapi.encoders import jsonable_encoder

from app.db.operations import stats as ops

logger = logging.getLogger(__name__)

SNAPSHOT_SUBDIR = "stats"


def snapshot_window(day: date) -> tuple[datetime, datetime]:
    """[00:00 of ``day``, 00:00 of the next day) in the repo's naive local time."""
    start = datetime.combine(day, datetime.min.time())
    return start, start + timedelta(days=1)


def snapshot_path(directory: str, day: date) -> str:
    return os.path.join(directory, SNAPSHOT_SUBDIR, f"{day.isoformat()}.json")


async def build_daily_snapshot(day: date, db) -> dict:
    """Compute every /stats aggregate for one calendar day."""
    start, end = snapshot_window(day)
    f = ops.StatsFilter(from_=start, to=end)

    review_quality = {
        group_by: await ops.review_quality(f, group_by, db)
        for group_by in ("group", "crop_model", "rotation_model")
    }
    return {
        "day": day.isoformat(),
        "from": start,
        "to": end,
        "created_at": datetime.now(),
        "overview": await ops.overview(f, db),
        "review_quality": review_quality,
        "anomalies": {
            "all": await ops.anomalies(f, None, db),
            "group": await ops.anomalies(f, "group", db),
        },
        "editor_usage": {"group": await ops.editor_usage(f, "group", db)},
        # Latest settings per user as of the snapshot (not date-filtered).
        "settings": await ops.settings_distribution(None, db),
    }


async def store_daily_snapshot(day: date, db) -> dict:
    """Compute and upsert the snapshot for ``day`` (idempotent per day)."""
    doc = await build_daily_snapshot(day, db)
    await db.stats_snapshots.update_one({"day": doc["day"]}, {"$set": doc}, upsert=True)
    return doc


def write_snapshot_file(doc: dict, directory: str) -> str:
    """Write the snapshot as pretty JSON under ``<directory>/stats/<day>.json``."""
    path = snapshot_path(directory, date.fromisoformat(doc["day"]))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = jsonable_encoder(doc, custom_encoder={ObjectId: str})
    payload.pop("_id", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


async def list_snapshots(from_day: date, to_day: date, db) -> list[dict]:
    """Snapshots with ``from_day <= day <= to_day``, oldest first."""
    if from_day > to_day:
        raise ValueError("'from' must not be after 'to'")
    cursor = db.stats_snapshots.find(
        {"day": {"$gte": from_day.isoformat(), "$lte": to_day.isoformat()}},
        {"_id": 0},
    ).sort("day", 1)
    return await cursor.to_list(length=None)
