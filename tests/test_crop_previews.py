"""Tests for the crop preview archive (``GET /{title_id}/crop-previews``)."""

import io
import sys
import types
import zipfile

import cv2
import numpy as np
from bson import ObjectId

# The titles router imports the Hatchet predict workflow, whose client demands a
# real JWT at import time. Stub it: this test never schedules a workflow.
sys.modules.setdefault(
    "app.tasks.workflows.predict_workflow",
    types.SimpleNamespace(predict_workflow=None),
)

from app.api.routes.titles import get_crop_previews_archive
from app.api.utils import crop_page, iter_crop_previews_archive
from app.db.schemas.title import Page, Scan

# ``@limiter.limit`` wraps the module-level name; call the bare route function.
previews_route = get_crop_previews_archive.__wrapped__


def _page(xc=0.5, yc=0.5, width=0.5, height=0.5, angle=0.0):
    return Page(xc=xc, yc=yc, width=width, height=height, angle=angle)


def _write_scan(tmp_path, name, width=200, height=100):
    path = tmp_path / f"{name or 'noname'}.jpg"
    image = np.full((height, width, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(path), image)
    return str(path)


def _decode(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def test_crop_page_keeps_stored_resolution():
    image = np.zeros((100, 200, 3), dtype=np.uint8)

    crop = crop_page(image, _page(width=0.5, height=0.5))

    assert crop.shape[:2] == (50, 100)


def test_crop_page_turns_crop_by_scan_orientation():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    # Mark the crop's top-left corner to follow it through the rotation.
    image[25:30, 50:55] = 255

    upright = crop_page(image, _page(), orientation=0)
    turned = crop_page(image, _page(), orientation=90)

    assert turned.shape[:2] == (100, 50)
    assert upright[0:5, 0:5].mean() > 200
    # 90° clockwise: the top-left corner ends up top-right.
    assert turned[0:5, -5:].mean() > 200


def test_crop_page_rotation_matches_cropilot_utils():
    # A tilted page drawn into the scan: undoing the crop angle (as
    # crop_documents() in cropilot-utils does) makes it fill the crop.
    image = np.zeros((400, 400, 3), dtype=np.uint8)
    page = _page(xc=0.5, yc=0.5, width=0.5, height=0.3, angle=10)
    rotation = cv2.getRotationMatrix2D((200, 200), -page.angle, 1.0)
    upright = np.zeros_like(image)
    upright[140:260, 100:300] = 255
    image = cv2.warpAffine(upright, rotation, (400, 400))

    crop = crop_page(image, page)

    assert crop[5:-5, 5:-5].mean() > 250


def test_iter_crop_previews_archive_names_and_page_choice(tmp_path):
    scans = [
        Scan(
            filename=_write_scan(tmp_path, "v0001"),
            scan_name="v0001",
            # Stored right page first: entries are ordered left to right.
            predicted_pages=[_page(xc=0.75, width=0.4), _page(xc=0.25, width=0.4)],
        ),
        Scan(
            filename=_write_scan(tmp_path, "v0002"),
            scan_name="v0002",
            predicted_pages=[_page(), _page()],
            user_edited_pages=[_page(width=0.3)],
        ),
        Scan(
            filename=_write_scan(tmp_path, ""),
            scan_name="",
            predicted_pages=[_page()],
        ),
    ]

    archive = zipfile.ZipFile(io.BytesIO(b"".join(iter_crop_previews_archive(scans))))

    assert archive.testzip() is None
    assert archive.namelist() == [
        "v0001_page1.jpg",
        "v0001_page2.jpg",
        "v0002_page1.jpg",  # user edits win over the two predictions
        "sken_0003_page1.jpg",  # empty scan_name falls back to the position
    ]
    edited = _decode(archive.read("v0002_page1.jpg"))
    assert edited.shape[:2] == (50, 60)


def test_iter_crop_previews_archive_skips_missing_and_empty_scans(tmp_path):
    scans = [
        Scan(
            filename=str(tmp_path / "gone.jpg"),
            scan_name="gone",
            predicted_pages=[_page()],
        ),
        Scan(filename=_write_scan(tmp_path, "empty"), scan_name="empty"),
        Scan(
            filename=_write_scan(tmp_path, "ok"),
            scan_name="ok",
            predicted_pages=[_page()],
        ),
    ]

    archive = zipfile.ZipFile(io.BytesIO(b"".join(iter_crop_previews_archive(scans))))

    assert archive.namelist() == ["ok_page1.jpg"]


class FakeTitles:
    def __init__(self, title):
        self.title = title

    async def find_one(self, query, projection=None):
        return self.title


class FakeDb:
    def __init__(self, title):
        self.titles = FakeTitles(title)


async def test_get_crop_previews_archive_streams_zip(tmp_path):
    title_id = ObjectId()
    scans = [
        {
            "_id": ObjectId(),
            "filename": _write_scan(tmp_path, name),
            "scan_name": name,
            "predicted_pages": [_page().model_dump(by_alias=True)],
        }
        for name in ("b", "a")
    ]
    db = FakeDb({"_id": title_id, "external_id": "Kniha č. 1", "scans": scans})

    response = await previews_route(request=None, title_id=str(title_id), db=db)

    assert response.media_type == "application/zip"
    disposition = response.headers["content-disposition"]
    assert f'filename="{title_id}_nahled.zip"' in disposition
    assert "filename*=UTF-8''Kniha%20%C4%8D.%201_nahled.zip" in disposition
    body = b"".join([chunk async for chunk in response.body_iterator])
    # Sorted by filename like /scans.
    assert zipfile.ZipFile(io.BytesIO(body)).namelist() == [
        "a_page1.jpg",
        "b_page1.jpg",
    ]


async def test_get_crop_previews_archive_unknown_title_is_404():
    from fastapi import HTTPException

    try:
        await previews_route(request=None, title_id=str(ObjectId()), db=FakeDb(None))
    except HTTPException as e:
        assert e.status_code == 404
    else:
        raise AssertionError("expected HTTPException(404)")
