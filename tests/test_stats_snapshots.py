import json
from datetime import date, datetime

from bson import ObjectId

from app.db.operations.stats_snapshots import (
    snapshot_path,
    snapshot_window,
    write_snapshot_file,
)


def test_snapshot_window_covers_one_local_day():
    start, end = snapshot_window(date(2026, 8, 31))
    assert start == datetime(2026, 8, 31, 0, 0)
    assert end == datetime(2026, 9, 1, 0, 0)


def test_snapshot_path_is_dated_json_under_stats_dir():
    assert snapshot_path("/scan_data/mongodump", date(2026, 8, 31)) == (
        "/scan_data/mongodump/stats/2026-08-31.json"
    )


def test_write_snapshot_file_serializes_bson_and_drops_mongo_id(tmp_path):
    doc = {
        "_id": ObjectId(),
        "day": "2026-08-31",
        "from": datetime(2026, 8, 31),
        "to": datetime(2026, 9, 1),
        "overview": {"active_users": 2},
        "review_quality": {"group": [{"key": str(ObjectId()), "key_name": "MZK"}]},
    }

    path = write_snapshot_file(doc, str(tmp_path))

    assert path == str(tmp_path / "stats" / "2026-08-31.json")
    data = json.loads(open(path, encoding="utf-8").read())
    assert "_id" not in data
    assert data["day"] == "2026-08-31"
    assert data["from"].startswith("2026-08-31T00:00:00")
    assert data["review_quality"]["group"][0]["key_name"] == "MZK"
