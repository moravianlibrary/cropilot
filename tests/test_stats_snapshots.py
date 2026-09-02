import json
from datetime import datetime

from bson import ObjectId

from app.db.operations.stats_snapshots import (
    snapshot_path,
    snapshot_window,
    write_snapshot_file,
)


def test_snapshot_window_is_trailing_days_ending_now():
    now = datetime(2026, 9, 2, 2, 0)
    start, end = snapshot_window(now, 30)
    assert start == datetime(2026, 8, 3, 2, 0)
    assert end == now


def test_snapshot_path_is_single_latest_file_under_stats_dir():
    assert (
        snapshot_path("/scan_data/mongodump")
        == "/scan_data/mongodump/stats/latest.json"
    )


def test_write_snapshot_file_overwrites_and_serializes_bson(tmp_path):
    doc = {
        "_id": "latest",
        "computed_at": datetime(2026, 9, 2, 2, 0),
        "overview": {"active_users": 2},
        "review_quality": {"group": [{"key": str(ObjectId()), "key_name": "MZK"}]},
    }

    write_snapshot_file(doc, str(tmp_path))
    doc["overview"]["active_users"] = 3
    path = write_snapshot_file(doc, str(tmp_path))

    assert path == str(tmp_path / "stats" / "latest.json")
    assert [p.name for p in (tmp_path / "stats").iterdir()] == ["latest.json"]
    data = json.loads(open(path, encoding="utf-8").read())
    assert "_id" not in data
    assert data["overview"]["active_users"] == 3
    assert data["computed_at"].startswith("2026-09-02T02:00:00")
    assert data["review_quality"]["group"][0]["key_name"] == "MZK"
