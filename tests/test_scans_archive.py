"""Tests for the streamed scan archive (``GET /{title_id}/archive``)."""

import io
import sys
import types
import zipfile
from pathlib import Path

from bson import ObjectId

# The titles router imports the Hatchet predict workflow, whose client demands a
# real JWT at import time. Stub it: this test never schedules a workflow.
sys.modules.setdefault(
    "app.tasks.workflows.predict_workflow",
    types.SimpleNamespace(predict_workflow=None),
)

from app.api.routes.titles import get_scans_archive
from app.api.utils import iter_scans_archive

# ``@limiter.limit`` wraps the module-level name; the router registered the bare
# function. Call the bare one, the slowapi wrapper needs a live Request.
archive_route = get_scans_archive.__wrapped__


def _write_scans(tmp_path, count):
    scans = []
    for i in range(count):
        path = tmp_path / f"scan-{i:03d}.jpg"
        path.write_bytes(b"\xff\xd8JPEG-PAYLOAD-%d" % i * 50)
        scans.append({"_id": ObjectId(), "filename": str(path)})
    return scans


def _read_zip(chunks):
    data = b"".join(chunks)
    return data, zipfile.ZipFile(io.BytesIO(data))


def test_iter_scans_archive_contains_every_scan_named_by_id(tmp_path):
    scans = _write_scans(tmp_path, 3)

    chunks = list(iter_scans_archive(scans))
    data, archive = _read_zip(chunks)

    assert archive.testzip() is None
    assert archive.namelist() == [f"{s['_id']}.jpg" for s in scans]
    for scan in scans:
        assert archive.read(f"{scan['_id']}.jpg") == Path(scan["filename"]).read_bytes()
    # Stored, not deflated: JPEGs do not compress.
    assert all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist())
    # One chunk per file plus the central directory -> streamed incrementally.
    assert len(chunks) == len(scans) + 1
    assert len(data) > sum(len(Path(s["filename"]).read_bytes()) for s in scans)


def test_iter_scans_archive_skips_missing_files(tmp_path):
    scans = _write_scans(tmp_path, 2)
    scans.insert(1, {"_id": ObjectId(), "filename": str(tmp_path / "gone.jpg")})

    _, archive = _read_zip(iter_scans_archive(scans))

    assert archive.namelist() == [f"{scans[0]['_id']}.jpg", f"{scans[2]['_id']}.jpg"]


def test_iter_scans_archive_empty_title_is_valid_zip():
    _, archive = _read_zip(iter_scans_archive([]))
    assert archive.namelist() == []


class FakeTitles:
    def __init__(self, title):
        self.title = title
        self.calls = []

    async def find_one(self, query, projection=None):
        self.calls.append((query, projection))
        return self.title


class FakeDb:
    def __init__(self, title):
        self.titles = FakeTitles(title)


async def test_get_scans_archive_streams_zip_sorted_by_filename(tmp_path):
    scans = _write_scans(tmp_path, 3)
    title_id = ObjectId()
    # Stored out of order on purpose; the route sorts by filename like /scans.
    db = FakeDb({"_id": title_id, "scans": list(reversed(scans))})

    response = await archive_route(request=None, title_id=str(title_id), db=db)

    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{title_id}.zip"'
    )
    body = b"".join([chunk async for chunk in response.body_iterator])
    archive = zipfile.ZipFile(io.BytesIO(body))
    assert archive.namelist() == [f"{s['_id']}.jpg" for s in scans]
    # Only the fields needed for the archive are projected.
    assert db.titles.calls[0][1] == {"scans._id": 1, "scans.filename": 1}


async def test_get_scans_archive_unknown_title_is_404():
    from fastapi import HTTPException

    try:
        await archive_route(request=None, title_id=str(ObjectId()), db=FakeDb(None))
    except HTTPException as e:
        assert e.status_code == 404
    else:
        raise AssertionError("expected HTTPException(404)")
