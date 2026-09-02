"""Aggregation pipelines behind the admin ``/stats`` endpoints.

Everything here returns aggregates per group / model / month only. Events
carry ``user_id`` (needed for distinct-user counts and the latest settings
snapshot per user), but no function exposes per-user rows.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from bson import ObjectId

from app.db.schemas.title import TaskState
from app.db.schemas.usage_event import HEARTBEAT_SECONDS, UsageEventType

GroupBy = Literal["group", "crop_model", "rotation_model", "month"]


@dataclass
class StatsFilter:
    from_: datetime
    to: datetime  # exclusive upper bound
    group_id: ObjectId | None = None
    crop_model: str | None = None
    rotation_model: str | None = None

    def title_match(self) -> dict:
        match: dict = {"modified_at": {"$gte": self.from_, "$lt": self.to}}
        if self.group_id:
            match["group_id"] = self.group_id
        if self.crop_model:
            match["settings.crop_model"] = self.crop_model
        if self.rotation_model:
            match["settings.rotation_model"] = self.rotation_model
        return match

    def event_match(self) -> dict:
        match: dict = {"ts": {"$gte": self.from_, "$lt": self.to}}
        if self.group_id:
            match["group_id"] = self.group_id
        return match


def make_filter(
    from_: datetime | None,
    to: datetime | None,
    group_id: str | None,
    crop_model: str | None,
    rotation_model: str | None,
    default_days: int = 30,
    max_days: int = 731,
) -> StatsFilter:
    """Normalize query params. A date-only ``to`` is treated as inclusive."""
    now = datetime.now()
    to = to or now
    if to.time() == datetime.min.time():
        to = to + timedelta(days=1)
    from_ = from_ or (to - timedelta(days=default_days))
    if from_ >= to:
        raise ValueError("'from' must be before 'to'")
    if to - from_ > timedelta(days=max_days):
        raise ValueError(f"Range longer than {max_days} days")
    gid = None
    if group_id:
        if not ObjectId.is_valid(group_id):
            raise ValueError("Invalid group_id")
        gid = ObjectId(group_id)
    return StatsFilter(from_, to, gid, crop_model or None, rotation_model or None)


def _key_expr(group_by: GroupBy | None, date_field: str) -> dict | str:
    if group_by == "group":
        return "$group_id"
    if group_by == "crop_model":
        return {"$ifNull": ["$settings.crop_model", "default"]}
    if group_by == "rotation_model":
        return {"$ifNull": ["$settings.rotation_model", "text"]}
    if group_by == "month":
        return {"$dateToString": {"format": "%Y-%m", "date": f"${date_field}"}}
    return {"$literal": "all"}


def _div(num, den) -> dict:
    """Safe division expression returning null when the denominator is 0."""
    return {
        "$cond": [{"$gt": [den, 0]}, {"$divide": [num, den]}, None],
    }


async def _resolve_keys(items: list[dict], group_by: GroupBy | None, db) -> list[dict]:
    """Turn ``_id`` into ``key``/``key_name``; group ids become group names."""
    names: dict[ObjectId, str] = {}
    if group_by == "group":
        ids = [i["_id"] for i in items if isinstance(i["_id"], ObjectId)]
        if ids:
            groups = await db.groups.find({"_id": {"$in": ids}}, {"name": 1}).to_list(
                length=None
            )
            names = {g["_id"]: g["name"] for g in groups}
    out = []
    for item in items:
        raw = item.pop("_id")
        key = str(raw) if raw is not None else "unknown"
        item["key"] = key
        item["key_name"] = names.get(raw, key) if isinstance(raw, ObjectId) else key
        out.append(item)
    out.sort(key=lambda i: i["key_name"])
    return out


async def _aggregate(collection, pipeline: list[dict]) -> list[dict]:
    """Run an aggregation to completion.

    With pymongo's async API ``aggregate()`` is itself a coroutine returning the
    cursor, so it has to be awaited before ``to_list``.
    """
    cursor = await collection.aggregate(pipeline)
    return await cursor.to_list(length=None)


def _round_floats(obj):
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------


async def overview(f: StatsFilter, db) -> dict:
    tmatch, ematch = f.title_match(), f.event_match()

    async def by_state():
        rows = await _aggregate(
            db.titles,
            [{"$match": tmatch}, {"$group": {"_id": "$state", "n": {"$sum": 1}}}],
        )
        counts = {s.value: 0 for s in TaskState}
        for r in rows:
            counts[r["_id"]] = r["n"]
        return counts

    async def scans():
        rows = await _aggregate(
            db.titles,
            [
                {"$match": tmatch},
                {
                    "$project": {
                        "n": {"$size": "$scans"},
                        "e": {
                            "$size": {
                                "$filter": {
                                    "input": "$scans",
                                    "cond": {"$ne": ["$$this.user_edited_pages", None]},
                                }
                            }
                        },
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total": {"$sum": "$n"},
                        "edited": {"$sum": "$e"},
                    }
                },
            ],
        )
        return (
            {"total": rows[0]["total"], "edited": rows[0]["edited"]}
            if rows
            else {
                "total": 0,
                "edited": 0,
            }
        )

    async def active_users():
        return len(await db.usage_events.distinct("user_id", ematch))

    async def sessions():
        return len(
            await db.usage_events.distinct(
                "session_id", {**ematch, "type": UsageEventType.editor_open.value}
            )
        )

    async def editor_time():
        n = await db.usage_events.count_documents(
            {**ematch, "type": UsageEventType.editor_heartbeat.value}
        )
        return n * HEARTBEAT_SECONDS

    states, scan_counts, users, sess, time_s = await asyncio.gather(
        by_state(), scans(), active_users(), sessions(), editor_time()
    )
    return {
        "titles_by_state": states,
        "scans": scan_counts,
        "active_users": users,
        "sessions": sess,
        "editor_time_s": time_s,
    }


async def review_quality(f: StatsFilter, group_by: GroupBy, db) -> list[dict]:
    """Per-key quality of predictions from the precomputed ``review_stats``."""

    def is_state(state: TaskState) -> dict:
        return {"$cond": [{"$eq": ["$state", state.value]}, 1, 0]}

    pipeline = [
        {"$match": {**f.title_match(), "review_stats": {"$ne": None}}},
        {
            "$group": {
                "_id": _key_expr(group_by, "modified_at"),
                "n_titles": {"$sum": 1},
                "mean_edit_ratio": {"$avg": "$review_stats.edit_ratio"},
                "mean_changed_ratio": {
                    "$avg": _div(
                        "$review_stats.scans_changed", "$review_stats.scans_total"
                    )
                },
                "mean_iou": {"$avg": "$review_stats.mean_iou"},
                "mean_center_shift": {"$avg": "$review_stats.mean_center_shift"},
                "mean_angle_delta": {"$avg": "$review_stats.mean_angle_delta"},
                "pages_added": {"$sum": "$review_stats.pages_added"},
                "pages_removed": {"$sum": "$review_stats.pages_removed"},
                "scans_total": {"$sum": "$review_stats.scans_total"},
                "scans_edited": {"$sum": "$review_stats.scans_edited"},
                "orientation_changed": {"$sum": "$review_stats.orientation_changed"},
                "retrain": {"$sum": is_state(TaskState.retrain)},
                "completed": {"$sum": is_state(TaskState.completed)},
                # Requires MongoDB >= 7.0 (deploy pins 8.2). On older servers
                # drop this accumulator and compute the median in Python.
                "median_turnaround_s": {
                    "$median": {
                        "input": {
                            "$cond": [
                                {"$and": ["$ready_at", "$user_approved_at"]},
                                {
                                    "$dateDiff": {
                                        "startDate": "$ready_at",
                                        "endDate": "$user_approved_at",
                                        "unit": "second",
                                    }
                                },
                                None,
                            ]
                        },
                        "method": "approximate",
                    }
                },
            }
        },
        {
            "$addFields": {
                "orientation_change_rate": _div("$orientation_changed", "$scans_total"),
                "retrain_rate": _div("$retrain", {"$add": ["$retrain", "$completed"]}),
            }
        },
    ]
    rows = await _aggregate(db.titles, pipeline)
    return _round_floats(await _resolve_keys(rows, group_by, db))


async def anomalies(f: StatsFilter, group_by: GroupBy | None, db) -> list[dict]:
    """How often flagged scans were actually edited (precision) and vice versa."""
    edited = {"$ne": ["$scans.user_edited_pages", None]}
    pipeline = [
        {
            "$match": {
                **f.title_match(),
                "state": {
                    "$in": [
                        TaskState.ready.value,
                        TaskState.user_approved.value,
                        TaskState.retrain.value,
                        TaskState.completed.value,
                    ]
                },
            }
        },
        {"$project": {"key": _key_expr(group_by, "modified_at"), "scans": 1}},
        {"$unwind": "$scans"},
        {
            "$project": {
                "key": 1,
                "edited": edited,
                "flags": {
                    "$setUnion": [
                        {
                            "$reduce": {
                                "input": "$scans.predicted_pages.flags",
                                "initialValue": [],
                                "in": {"$concatArrays": ["$$value", "$$this"]},
                            }
                        }
                    ]
                },
            }
        },
        {"$addFields": {"flagged": {"$gt": [{"$size": "$flags"}, 0]}}},
        {
            "$facet": {
                "totals": [
                    {
                        "$group": {
                            "_id": "$key",
                            "scans": {"$sum": 1},
                            "edited": {"$sum": {"$cond": ["$edited", 1, 0]}},
                            "flagged": {"$sum": {"$cond": ["$flagged", 1, 0]}},
                            "flagged_edited": {
                                "$sum": {
                                    "$cond": [{"$and": ["$flagged", "$edited"]}, 1, 0]
                                }
                            },
                        }
                    }
                ],
                "per_flag": [
                    {"$unwind": "$flags"},
                    {
                        "$group": {
                            "_id": {"key": "$key", "flag": "$flags"},
                            "n": {"$sum": 1},
                            "edited": {"$sum": {"$cond": ["$edited", 1, 0]}},
                        }
                    },
                ],
            }
        },
    ]
    rows = await _aggregate(db.titles, pipeline)
    if not rows:
        return []
    totals, per_flag = rows[0]["totals"], rows[0]["per_flag"]

    flags_by_key: dict = {}
    for r in per_flag:
        entry = flags_by_key.setdefault(r["_id"]["key"], {})
        n, e = r["n"], r["edited"]
        entry[r["_id"]["flag"]] = {
            "n": n,
            "edited": e,
            "edit_rate": e / n if n else None,
        }

    items = []
    for t in totals:
        flagged, edited_n, fe = t["flagged"], t["edited"], t["flagged_edited"]
        items.append(
            {
                "_id": t["_id"],
                "scans": t["scans"],
                "edited": edited_n,
                "flagged": flagged,
                "flagged_edited": fe,
                "unflagged_edited": edited_n - fe,
                "precision": fe / flagged if flagged else None,
                "recall": fe / edited_n if edited_n else None,
                "flags": flags_by_key.get(t["_id"], {}),
            }
        )
    return _round_floats(await _resolve_keys(items, group_by, db))


async def editor_usage(
    f: StatsFilter, group_by: Literal["group", "month"], db, top_n: int = 20
) -> list[dict]:
    ematch = f.event_match()

    def is_type(t: UsageEventType) -> dict:
        return {"$cond": [{"$eq": ["$type", t.value]}, 1, 0]}

    # Sessions: one row per session_id, then aggregated per key.
    session_key = (
        "$group_id"
        if group_by == "group"
        else {"$dateToString": {"format": "%Y-%m", "date": "$first_ts"}}
    )
    sessions_pipeline = [
        {"$match": ematch},
        {
            "$group": {
                "_id": "$session_id",
                "group_id": {"$first": "$group_id"},
                "first_ts": {"$min": "$ts"},
                "heartbeats": {"$sum": is_type(UsageEventType.editor_heartbeat)},
                "saves": {"$sum": is_type(UsageEventType.save)},
                "shortcuts": {"$sum": is_type(UsageEventType.shortcut)},
                "mouse": {"$sum": is_type(UsageEventType.mouse_action)},
                "filters": {"$sum": is_type(UsageEventType.filter_change)},
            }
        },
        {
            "$addFields": {
                "duration_s": {"$multiply": ["$heartbeats", HEARTBEAT_SECONDS]}
            }
        },
        {
            "$group": {
                "_id": session_key,
                "sessions": {"$sum": 1},
                "total_duration_s": {"$sum": "$duration_s"},
                "mean_duration_s": {"$avg": "$duration_s"},
                "saves": {"$sum": "$saves"},
                "shortcuts": {"$sum": "$shortcuts"},
                "mouse_actions": {"$sum": "$mouse"},
                "filter_changes": {"$sum": "$filters"},
            }
        },
        {
            "$addFields": {
                "saves_per_session": _div("$saves", "$sessions"),
                "keyboard_ratio": _div(
                    "$shortcuts", {"$add": ["$shortcuts", "$mouse_actions"]}
                ),
            }
        },
    ]

    event_key = _key_expr(group_by, "ts")
    actions_pipeline = [
        {
            "$match": {
                **ematch,
                "type": {
                    "$in": [
                        UsageEventType.shortcut.value,
                        UsageEventType.mouse_action.value,
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": {"key": event_key, "action": "$payload.action", "type": "$type"},
                "n": {"$sum": 1},
            }
        },
    ]
    filters_pipeline = [
        {"$match": {**ematch, "type": UsageEventType.filter_change.value}},
        {
            "$group": {
                "_id": {
                    "key": event_key,
                    "filter": "$payload.filter",
                    "value": {"$toString": "$payload.value"},
                },
                "n": {"$sum": 1},
            }
        },
    ]

    sessions, actions, filters = await asyncio.gather(
        _aggregate(db.usage_events, sessions_pipeline),
        _aggregate(db.usage_events, actions_pipeline),
        _aggregate(db.usage_events, filters_pipeline),
    )

    actions_by_key: dict = {}
    for r in actions:
        k, action, typ = r["_id"]["key"], r["_id"]["action"], r["_id"]["type"]
        entry = actions_by_key.setdefault(k, {}).setdefault(
            action, {"action": action, "keyboard": 0, "mouse": 0}
        )
        entry["keyboard" if typ == UsageEventType.shortcut.value else "mouse"] += r["n"]

    filters_by_key: dict = {}
    for r in filters:
        k, flt, val = r["_id"]["key"], r["_id"]["filter"], r["_id"]["value"]
        filters_by_key.setdefault(k, {}).setdefault(flt, {})[val] = r["n"]

    for s in sessions:
        k = s["_id"]
        per_action = list(actions_by_key.get(k, {}).values())
        per_action.sort(key=lambda a: -(a["keyboard"] + a["mouse"]))
        s["actions"] = per_action
        s["top_shortcuts"] = sorted(
            (
                {"action": a["action"], "n": a["keyboard"]}
                for a in per_action
                if a["keyboard"]
            ),
            key=lambda a: -a["n"],
        )[:top_n]
        s["filters"] = filters_by_key.get(k, {})

    return _round_floats(await _resolve_keys(sessions, group_by, db))


async def settings_distribution(group_id: ObjectId | None, db) -> dict:
    """Distribution of each editor setting over the latest snapshot per user."""
    match: dict = {"type": UsageEventType.settings_snapshot.value}
    if group_id:
        match["group_id"] = group_id
    pipeline = [
        {"$match": match},
        # Events of one batch share the server ``ts``; ObjectIds are generated in
        # insertion order, so ``_id`` breaks the tie towards the latest snapshot.
        {"$sort": {"user_id": 1, "ts": -1, "_id": -1}},
        {"$group": {"_id": "$user_id", "payload": {"$first": "$payload"}}},
        {
            "$facet": {
                "users": [{"$count": "n"}],
                "kv": [
                    {"$project": {"kv": {"$objectToArray": "$payload"}}},
                    {"$unwind": "$kv"},
                    {
                        "$group": {
                            "_id": {"k": "$kv.k", "v": {"$toString": "$kv.v"}},
                            "n": {"$sum": 1},
                        }
                    },
                ],
            }
        },
    ]
    rows = await _aggregate(db.usage_events, pipeline)
    if not rows:
        return {"users_total": 0, "settings": {}}
    users = rows[0]["users"][0]["n"] if rows[0]["users"] else 0
    settings: dict[str, dict[str, int]] = {}
    for r in rows[0]["kv"]:
        settings.setdefault(r["_id"]["k"], {})[r["_id"]["v"]] = r["n"]
    return {"users_total": users, "settings": settings}
