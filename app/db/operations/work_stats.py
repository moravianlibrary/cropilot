"""Incremental roll-up of editor usage events into ``Title.work_stats``.

Usage events expire (TTL), so the human time spent on a title is accumulated
onto the title itself: every run takes the events with ``ts`` in
``(events_until, now - lag]``, sums heartbeats / editor opens per title and
``$inc``s them into ``work_stats``. The high-water mark lives in
``stats_snapshots`` / ``_id = "work_stats_rollup"`` and is advanced only after
the titles were written, so a crashed run is replayed rather than lost. The
lag keeps a batch that is in flight at run time from slipping past the mark.
"""

import logging
from datetime import datetime, timedelta

from pymongo import UpdateOne

from app.db.schemas.usage_event import HEARTBEAT_SECONDS, UsageEventType

logger = logging.getLogger(__name__)

ROLLUP_ID = "work_stats_rollup"
SAFETY_LAG = timedelta(minutes=5)


def rollup_pipeline(since: datetime | None, until: datetime) -> list[dict]:
    """Per-title sums of the events in ``[since, until)`` (``since`` optional)."""
    is_heartbeat = {"$eq": ["$type", UsageEventType.editor_heartbeat.value]}
    is_open = {"$eq": ["$type", UsageEventType.editor_open.value]}
    ts: dict = {"$lt": until}
    if since is not None:
        ts["$gte"] = since
    return [
        {
            "$match": {
                "title_id": {"$ne": None},
                "ts": ts,
                "type": {
                    "$in": [
                        UsageEventType.editor_heartbeat.value,
                        UsageEventType.editor_open.value,
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": "$title_id",
                "active": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    is_heartbeat,
                                    # Missing flag (older frontend) = active.
                                    {"$ne": ["$payload.active", False]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "visible": {"$sum": {"$cond": [is_heartbeat, 1, 0]}},
                "sessions": {"$sum": {"$cond": [is_open, 1, 0]}},
                # $min ignores nulls, so non-open events don't pull it down.
                "first_opened_at": {"$min": {"$cond": [is_open, "$ts", None]}},
                "last_activity_at": {"$max": "$ts"},
            }
        },
    ]


def title_update(row: dict, now: datetime) -> UpdateOne:
    """Pipeline update: titles are inserted with ``work_stats: null`` and the
    plain ``$inc`` / ``$min`` operators cannot work with that (``$inc`` cannot
    create a field inside null, and ``$min`` keeps an existing null because
    null sorts before dates). ``$ifNull`` makes every field start from empty."""

    def add(field: str, n: int) -> dict:
        return {"$add": [{"$ifNull": [f"$work_stats.{field}", 0]}, n]}

    last = row["last_activity_at"]
    fields: dict = {
        "work_stats.active_s": add("active_s", row["active"] * HEARTBEAT_SECONDS),
        "work_stats.visible_s": add("visible_s", row["visible"] * HEARTBEAT_SECONDS),
        "work_stats.sessions": add("sessions", row["sessions"]),
        "work_stats.last_activity_at": {
            "$max": [{"$ifNull": ["$work_stats.last_activity_at", last]}, last]
        },
        "work_stats.computed_at": now,
    }
    first = row.get("first_opened_at")
    if first is not None:
        fields["work_stats.first_opened_at"] = {
            "$min": [{"$ifNull": ["$work_stats.first_opened_at", first]}, first]
        }
    return UpdateOne({"_id": row["_id"]}, [{"$set": fields}])


async def rollup_work_stats(db, now: datetime | None = None) -> dict:
    """Fold new events into ``work_stats`` and advance the high-water mark."""
    now = now or datetime.now()
    until = now - SAFETY_LAG
    marker = await db.stats_snapshots.find_one({"_id": ROLLUP_ID})
    since = marker["events_until"] if marker else None
    if since is not None and since >= until:
        return {"titles": 0, "events_from": since, "events_until": since}

    cursor = await db.usage_events.aggregate(rollup_pipeline(since, until))
    rows = await cursor.to_list(length=None)

    if rows:
        await db.titles.bulk_write([title_update(r, now) for r in rows], ordered=False)

    await db.stats_snapshots.replace_one(
        {"_id": ROLLUP_ID},
        {
            "_id": ROLLUP_ID,
            "events_until": until,
            "computed_at": now,
            "titles": len(rows),
        },
        upsert=True,
    )
    logger.info(f"work_stats rollup: {len(rows)} titles, events {since} -> {until}")
    return {"titles": len(rows), "events_from": since, "events_until": until}
