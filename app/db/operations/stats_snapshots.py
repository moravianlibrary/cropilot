"""Latest statistics snapshot, refreshed by the maintenance cron.

Raw usage events expire (TTL) and the nightly mongodump overwrites itself, so
the maintenance cron stores the *current* aggregates in a single document
(``stats_snapshots`` / ``_id = "latest"``, no TTL, included in the dump) and
writes the same document to ``<dump>/stats/latest.json``. Both are overwritten
on every run; no history accumulates.
"""

import json
import logging
import os
from datetime import datetime, timedelta

from bson import ObjectId
from fastapi.encoders import jsonable_encoder

from app.db.operations import stats as ops

logger = logging.getLogger(__name__)

SNAPSHOT_ID = "latest"
SNAPSHOT_SUBDIR = "stats"
SNAPSHOT_FILENAME = "latest.json"
# Same default window as the Statistics page.
SNAPSHOT_WINDOW_DAYS = 30


def snapshot_window(
    now: datetime, days: int = SNAPSHOT_WINDOW_DAYS
) -> tuple[datetime, datetime]:
    """[now - days, now) in the repo's naive local time."""
    return now - timedelta(days=days), now


def snapshot_path(directory: str) -> str:
    return os.path.join(directory, SNAPSHOT_SUBDIR, SNAPSHOT_FILENAME)


async def build_snapshot(
    db, days: int = SNAPSHOT_WINDOW_DAYS, now: datetime | None = None
) -> dict:
    """Compute every /stats aggregate over the trailing ``days`` window."""
    now = now or datetime.now()
    start, end = snapshot_window(now, days)
    f = ops.StatsFilter(from_=start, to=end)

    review_quality = {
        group_by: await ops.review_quality(f, group_by, db)
        for group_by in ("group", "crop_model", "rotation_model")
    }
    return {
        "_id": SNAPSHOT_ID,
        "computed_at": now,
        "window_days": days,
        "from": start,
        "to": end,
        "overview": await ops.overview(f, db),
        "review_quality": review_quality,
        "anomalies": {
            "all": await ops.anomalies(f, None, db),
            "group": await ops.anomalies(f, "group", db),
        },
        "editor_usage": {"group": await ops.editor_usage(f, "group", db)},
        # Latest settings per user (not date-filtered).
        "settings": await ops.settings_distribution(None, db),
    }


async def store_snapshot(db, days: int = SNAPSHOT_WINDOW_DAYS) -> dict:
    """Compute the current aggregates and replace the single stored document."""
    doc = await build_snapshot(db, days)
    await db.stats_snapshots.replace_one({"_id": SNAPSHOT_ID}, doc, upsert=True)
    return doc


def write_snapshot_file(doc: dict, directory: str) -> str:
    """Overwrite ``<directory>/stats/latest.json`` with the snapshot."""
    path = snapshot_path(directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = jsonable_encoder(doc, custom_encoder={ObjectId: str})
    payload.pop("_id", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


async def get_snapshot(db) -> dict | None:
    doc = await db.stats_snapshots.find_one({"_id": SNAPSHOT_ID}, {"_id": 0})
    return doc
