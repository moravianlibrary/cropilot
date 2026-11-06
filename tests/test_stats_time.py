from datetime import datetime

from bson import ObjectId

from app.db.operations.stats import StatsFilter, _seconds_between, time_stats


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        return self.docs


class FakeColl:
    def __init__(self, rows):
        self.rows = rows
        self.pipeline = None

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return FakeCursor(self.rows)

    def find(self, query, projection=None):
        return FakeCursor([{"_id": i, "name": "MZK"} for i in query["_id"]["$in"]])


class FakeDb:
    def __init__(self, rows):
        self.titles = FakeColl(rows)
        self.groups = FakeColl([])


def test_seconds_between_guards_missing_and_inverted_timestamps():
    expr = _seconds_between("$a", "$b")
    cond = expr["$cond"]
    assert cond[0] == {"$and": ["$a", "$b", {"$gte": ["$b", "$a"]}]}
    assert cond[1]["$dateDiff"]["unit"] == "second"
    assert cond[2] is None


async def test_time_stats_resolves_group_names_and_rounds():
    gid = ObjectId()
    db = FakeDb(
        [
            {
                "_id": gid,
                "n_titles": 3,
                "scans_total": 300,
                "scans_changed": 90,
                "titles_with_machine": 2,
                "machine_scans": 200,
                "machine_total_s": 361,
                "titles_with_review": 3,
                "reviewed_scans": 300,
                "reviewed_scans_changed": 90,
                "review_active_total_s": 7800,
                "review_visible_total_s": 9000,
                "sessions": 5,
                "median_queue_s": 240,
                "median_processing_s": 180,
                "median_wait_review_s": 93600,
                "median_review_active_s": 2600,
                "median_lead_s": 140000,
                "median_after_save_s": None,
                "machine_s_per_scan": 1.805,
                "review_s_per_scan": 26.0,
                "review_s_per_changed_scan": 86.66666,
                "human_machine_ratio": 21.606648,
            }
        ]
    )
    f = StatsFilter(datetime(2026, 10, 1), datetime(2026, 11, 1))
    rows = await time_stats(f, "group", db)

    assert db.titles.pipeline[0] == {"$match": f.title_match()}
    group = db.titles.pipeline[2]["$group"]
    assert group["_id"] == "$key"
    assert "median_wait_review_s" in group

    assert len(rows) == 1
    row = rows[0]
    assert row["key"] == str(gid)
    assert row["key_name"] == "MZK"
    assert row["review_s_per_changed_scan"] == 86.6667
    assert row["human_machine_ratio"] == 21.6066
    assert row["median_after_save_s"] is None
