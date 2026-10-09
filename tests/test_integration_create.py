"""Tests for the integration create route (``POST /integration/create``)."""

import sys
import types

import pytest
from bson import ObjectId
from fastapi import HTTPException

# The integration router imports the Hatchet preprocess workflow, whose client
# demands a real JWT at import time. Stub it: these tests fail before scheduling.
sys.modules.setdefault(
    "app.tasks.workflows.preprocess_workflow",
    types.SimpleNamespace(preprocess_workflow=None),
)

from app.api import utils
from app.api.routes import integration
from app.db.schemas.title import Settings, TitleCreate


class FakeResult:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class FakeCursor:
    async def to_list(self, length=None):
        return []


class FakeTitles:
    def __init__(self):
        self.docs = {}

    def find(self, query, projection):
        return FakeCursor()

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = doc

    async def update_many(self, filter_, update):
        return FakeResult(sum(1 for _id in filter_["_id"]["$in"] if _id in self.docs))

    async def delete_one(self, filter_):
        self.docs.pop(filter_["_id"], None)


class FakeGroups:
    def __init__(self, exists):
        self.exists = exists
        self.pulled = []

    async def find_one(self, query, projection):
        return {"default_settings": Settings().model_dump()}

    async def update_one(self, filter_, update):
        if "$pull" in update:
            self.pulled.append(update["$pull"]["title_ids"])
        return FakeResult(1 if self.exists else 0)


class FakeDb:
    def __init__(self, group_exists):
        self.titles = FakeTitles()
        self.groups = FakeGroups(group_exists)


@pytest.fixture
def scans_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(integration, "UPLOAD_VOLUME_PATH", str(tmp_path))
    monkeypatch.setattr(utils, "UPLOAD_VOLUME_PATH", str(tmp_path))
    return tmp_path


async def test_create_title_cleans_up_when_linking_to_group_fails(scans_volume):
    db = FakeDb(group_exists=False)

    with pytest.raises(HTTPException) as exc_info:
        await integration.create_title(
            str(ObjectId()), TitleCreate(external_id="ext-1"), db
        )

    assert exc_info.value.status_code == 400
    assert "not found" in exc_info.value.detail
    assert db.titles.docs == {}
    assert len(db.groups.pulled) == 1
    assert list(scans_volume.iterdir()) == []
