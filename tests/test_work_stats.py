from datetime import datetime, timedelta

from bson import ObjectId

from app.db.operations.work_stats import (
    ROLLUP_ID,
    SAFETY_LAG,
    rollup_pipeline,
    rollup_work_stats,
    title_update,
)
from app.db.schemas.usage_event import HEARTBEAT_SECONDS


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        return self.docs


class FakeEvents:
    def __init__(self, rows):
        self.rows = rows
        self.pipeline = None

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return FakeCursor(self.rows)


class FakeTitles:
    def __init__(self):
        self.bulk = None

    async def bulk_write(self, ops, ordered=True):
        self.bulk = ops


class FakeSnapshots:
    def __init__(self, marker=None):
        self.marker = marker
        self.replaced = None

    async def find_one(self, query):
        return self.marker if query["_id"] == ROLLUP_ID else None

    async def replace_one(self, query, doc, upsert=False):
        self.replaced = doc


class FakeDb:
    def __init__(self, rows, marker=None):
        self.usage_events = FakeEvents(rows)
        self.titles = FakeTitles()
        self.stats_snapshots = FakeSnapshots(marker)


NOW = datetime(2026, 11, 7, 2, 0)


def test_pipeline_window_is_half_open_and_optional_since():
    until = datetime(2026, 11, 7, 1, 55)
    match = rollup_pipeline(None, until)[0]["$match"]
    assert match["ts"] == {"$lt": until}
    assert match["title_id"] == {"$ne": None}
    since = datetime(2026, 11, 6, 1, 55)
    assert rollup_pipeline(since, until)[0]["$match"]["ts"] == {
        "$lt": until,
        "$gte": since,
    }


def test_title_update_is_null_safe_and_keeps_first_open():
    tid = ObjectId()
    opened = datetime(2026, 11, 6, 9, 0)
    last = datetime(2026, 11, 6, 9, 30)
    op = title_update(
        {
            "_id": tid,
            "active": 10,
            "visible": 12,
            "sessions": 2,
            "first_opened_at": opened,
            "last_activity_at": last,
        },
        NOW,
    )
    assert op._filter == {"_id": tid}
    fields = op._doc[0]["$set"]
    # Titles start with work_stats: null, so every field starts from empty.
    assert fields["work_stats.active_s"] == {
        "$add": [{"$ifNull": ["$work_stats.active_s", 0]}, 10 * HEARTBEAT_SECONDS]
    }
    assert fields["work_stats.visible_s"]["$add"][1] == 12 * HEARTBEAT_SECONDS
    assert fields["work_stats.sessions"]["$add"][1] == 2
    assert fields["work_stats.first_opened_at"] == {
        "$min": [{"$ifNull": ["$work_stats.first_opened_at", opened]}, opened]
    }
    assert fields["work_stats.last_activity_at"] == {
        "$max": [{"$ifNull": ["$work_stats.last_activity_at", last]}, last]
    }
    assert fields["work_stats.computed_at"] == NOW

    # A window with heartbeats but no editor_open must not touch first_opened_at.
    op = title_update(
        {
            "_id": tid,
            "active": 1,
            "visible": 1,
            "sessions": 0,
            "first_opened_at": None,
            "last_activity_at": NOW,
        },
        NOW,
    )
    assert "work_stats.first_opened_at" not in op._doc[0]["$set"]


async def test_first_run_folds_everything_and_sets_marker():
    tid = ObjectId()
    db = FakeDb(
        [
            {
                "_id": tid,
                "active": 4,
                "visible": 6,
                "sessions": 1,
                "first_opened_at": NOW - timedelta(hours=5),
                "last_activity_at": NOW - timedelta(hours=4),
            }
        ]
    )
    result = await rollup_work_stats(db, now=NOW)

    until = NOW - SAFETY_LAG
    assert result == {"titles": 1, "events_from": None, "events_until": until}
    assert "$gte" not in db.usage_events.pipeline[0]["$match"]["ts"]
    assert len(db.titles.bulk) == 1
    assert db.titles.bulk[0]._filter == {"_id": tid}
    assert db.stats_snapshots.replaced["events_until"] == until


async def test_rerun_within_lag_is_a_noop():
    marker = {"_id": ROLLUP_ID, "events_until": NOW - timedelta(minutes=2)}
    db = FakeDb([{"_id": ObjectId()}], marker)
    result = await rollup_work_stats(db, now=NOW)
    assert result["titles"] == 0
    assert db.usage_events.pipeline is None
    assert db.stats_snapshots.replaced is None


async def test_incremental_run_starts_at_marker():
    since = NOW - timedelta(days=1)
    db = FakeDb([], {"_id": ROLLUP_ID, "events_until": since})
    result = await rollup_work_stats(db, now=NOW)
    assert result["events_from"] == since
    assert db.usage_events.pipeline[0]["$match"]["ts"]["$gte"] == since
    assert db.titles.bulk is None
    assert db.stats_snapshots.replaced["events_until"] == NOW - SAFETY_LAG
