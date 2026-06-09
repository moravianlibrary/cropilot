from bson import ObjectId
import pytest

from app.db.operations.api import link_titles_to_group_bulk, set_default_title_params
from app.db.schemas.title import Settings, Title


class FakeUpdateResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class FakeTitles:
    def __init__(self, matched_count):
        self.matched_count = matched_count
        self.updated_filter = None
        self.updated_value = None

    async def update_many(self, filter_, update):
        self.updated_filter = filter_
        self.updated_value = update
        return FakeUpdateResult(self.matched_count)


class FakeGroups:
    def __init__(self, default_settings=None, matched_count=1):
        self.default_settings = default_settings or Settings().model_dump()
        self.matched_count = matched_count
        self.updated_filter = None
        self.updated_value = None

    async def find_one(self, query, projection):
        return {"default_settings": self.default_settings}

    async def update_one(self, filter_, update):
        self.updated_filter = filter_
        self.updated_value = update
        return FakeUpdateResult(self.matched_count)


class FakeDb:
    def __init__(self, title_matches=1, group_matches=1):
        self.titles = FakeTitles(title_matches)
        self.groups = FakeGroups(matched_count=group_matches)


async def test_set_default_title_params_assigns_group_before_insert():
    group_id = str(ObjectId())
    title = Title()

    updated = await set_default_title_params(title, group_id, FakeDb())

    assert updated.group_id == ObjectId(group_id)
    assert updated.external_id == str(updated.id)
    assert updated.settings == Settings()


async def test_link_titles_to_group_updates_title_and_group():
    title_id = ObjectId()
    group_id = ObjectId()
    db = FakeDb()

    result = await link_titles_to_group_bulk([title_id], group_id, db)

    assert result == {"title_ids": [title_id], "group_id": group_id}
    assert db.titles.updated_filter == {"_id": {"$in": [title_id]}}
    assert db.titles.updated_value == {"$set": {"group_id": group_id}}
    assert db.groups.updated_filter == {"_id": group_id}
    assert db.groups.updated_value["$addToSet"] == {
        "title_ids": {"$each": [title_id]}
    }


async def test_link_titles_to_group_fails_when_title_update_matches_too_few():
    with pytest.raises(ValueError, match="matched 0/1 titles"):
        await link_titles_to_group_bulk([ObjectId()], ObjectId(), FakeDb(title_matches=0))


async def test_link_titles_to_group_fails_when_group_is_missing():
    with pytest.raises(ValueError, match="group .* not found"):
        await link_titles_to_group_bulk([ObjectId()], ObjectId(), FakeDb(group_matches=0))
