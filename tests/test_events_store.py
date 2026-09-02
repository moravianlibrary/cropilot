from datetime import datetime

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api.routes.events import post_events
from app.db.operations.events import store_events
from app.db.schemas.usage_event import UsageEventIn, UsageEventType
from app.db.schemas.user import Role, User


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        return self.docs


class FakeTitles:
    def __init__(self, docs):
        self.docs = docs
        self.query = None

    def find(self, query, projection=None):
        self.query = query
        ids = set(query["_id"]["$in"])
        return FakeCursor([d for d in self.docs if d["_id"] in ids])


class FakeEvents:
    def __init__(self):
        self.inserted = []

    async def insert_many(self, docs, ordered=True):
        self.inserted.extend(docs)


class FakeDb:
    def __init__(self, titles):
        self.titles = FakeTitles(titles)
        self.usage_events = FakeEvents()


def make_user(email="alice@example.com", role=Role.user) -> User:
    return User(_id=ObjectId(), email=email, full_name="Alice", role=role, password="x")


def event(**kwargs) -> UsageEventIn:
    base = {
        "type": "editor_heartbeat",
        "session_id": "session-0001",
        "payload": {"active": True},
    }
    base.update(kwargs)
    return UsageEventIn(**base)


async def test_store_events_sets_user_and_resolves_group_from_title():
    title_id, group_id = ObjectId(), ObjectId()
    db = FakeDb([{"_id": title_id, "group_id": group_id}])
    user = make_user()
    before = datetime.now()

    n = await store_events(
        [event(title_id=str(title_id)), event(title_id=str(ObjectId()))], user, db
    )

    assert n == 2
    known, unknown = db.usage_events.inserted
    assert known["user_id"] == user.id
    assert known["group_id"] == group_id
    assert known["title_id"] == title_id
    assert known["ts"] >= before
    assert known["type"] == UsageEventType.editor_heartbeat
    assert unknown["group_id"] is None  # unknown title must not fail the batch
    assert db.titles.query["_id"]["$in"]  # one lookup for the whole batch


async def test_store_events_ignores_client_supplied_identity_fields():
    db = FakeDb([])
    user = make_user()
    # Client cannot smuggle its own user_id: UsageEventIn has no such field,
    # extra keys are ignored by pydantic and never reach the document.
    e = UsageEventIn.model_validate(
        {
            "type": "shortcut",
            "session_id": "session-0001",
            "payload": {"action": "nav_next", "key": "ArrowRight"},
            "user_id": str(ObjectId()),
        }
    )
    await store_events([e], user, db)
    assert db.usage_events.inserted[0]["user_id"] == user.id


async def test_store_events_empty_batch_is_noop():
    db = FakeDb([])
    assert await store_events([], make_user(), db) == 0
    assert db.usage_events.inserted == []


def test_payload_validation_rejects_missing_required_key():
    with pytest.raises(ValueError):
        UsageEventIn(type="shortcut", session_id="session-0001", payload={"key": "x"})


def test_payload_validation_rejects_nested_objects():
    with pytest.raises(ValueError):
        event(payload={"active": True, "nested": {"a": 1}})


def test_payload_validation_rejects_too_many_keys():
    with pytest.raises(ValueError):
        event(payload={"active": True, **{f"k{i}": i for i in range(40)}})


class FakeRequest:
    def __init__(self, content_length=None):
        self.headers = (
            {} if content_length is None else {"content-length": str(content_length)}
        )


async def test_post_events_rejects_api_key_and_public_users():
    for email in ("api@request.user", "public@user.cropilot"):
        with pytest.raises(HTTPException) as exc:
            await post_events.__wrapped__(
                FakeRequest(), [event()], make_user(email=email), FakeDb([])
            )
        assert exc.value.status_code == 403


async def test_post_events_rejects_oversized_body():
    with pytest.raises(HTTPException) as exc:
        await post_events.__wrapped__(
            FakeRequest(content_length=2 * 1024 * 1024),
            [event()],
            make_user(),
            FakeDb([]),
        )
    assert exc.value.status_code == 413


async def test_post_events_accepts_signed_in_user():
    db = FakeDb([])
    result = await post_events.__wrapped__(
        FakeRequest(), [event(), event()], make_user(), db
    )
    assert result == {"accepted": 2}
    assert len(db.usage_events.inserted) == 2
