"""Pages of a scan are ordered left to right as the scan is displayed.

Crops are stored in the original (unrotated) frame. For a scan turned 90° in
the editor the two pages of a spread sit above each other in that frame, with
nearly equal ``xc``: sorting by ``xc`` put the right page first for ~half of the
spreads of title 075-8_TIFx (reviewer report, 2026-11-05).
"""

import pytest
from bson import ObjectId

from app.api.utils import (
    format_pages,
    format_pages_integration,
    oriented_xc,
    sort_pages_left_to_right,
)
from app.db.schemas.title import Page, Scan


def _page(xc, yc, width=0.4, height=0.4):
    return Page(xc=xc, yc=yc, width=width, height=height)


# Stored geometry of a real two-page scan from 075-8_TIFx (DSC_9302, 90°).
UPPER = _page(xc=0.4981, yc=0.2885)  # displayed on the right after a 90° turn
LOWER = _page(xc=0.4959, yc=0.7141)  # displayed on the left


@pytest.mark.parametrize(
    ("orientation", "left", "right"),
    [
        (0, _page(0.25, 0.5), _page(0.75, 0.5)),
        (90, LOWER, UPPER),
        (180, _page(0.75, 0.5), _page(0.25, 0.5)),
        (270, _page(0.5, 0.25), _page(0.5, 0.75)),
    ],
)
def test_sort_follows_displayed_orientation(orientation, left, right):
    assert oriented_xc(left, orientation) < oriented_xc(right, orientation)
    assert sort_pages_left_to_right([right, left], orientation) == [left, right]
    assert sort_pages_left_to_right([left, right], orientation) == [left, right]


def test_sort_by_stored_xc_would_put_right_page_first():
    # The regression: the stored xc of the right page is the smaller one.
    assert UPPER.xc > LOWER.xc
    assert sort_pages_left_to_right([UPPER, LOWER], 0) == [LOWER, UPPER]
    assert sort_pages_left_to_right([UPPER, LOWER], 90) == [LOWER, UPPER]


def _scan(pages, orientation, edited=True):
    return Scan(
        _id=ObjectId(),
        filename="/scans/DSC_9302.jpg",
        scan_name="DSC_9302",
        predicted_pages=[] if edited else pages,
        user_edited_pages=pages if edited else None,
        orientation=orientation,
    )


def test_format_pages_orders_rotated_scan():
    [formatted] = format_pages([_scan([UPPER, LOWER], 90)])
    assert [p["yc"] for p in formatted["pages"]] == [LOWER.yc, UPPER.yc]


def test_format_pages_integration_labels_rotated_spread():
    pages = format_pages_integration([_scan([UPPER, LOWER], 90)], ["DSC_9302.tif"])
    assert [(p["type"], p["yc"]) for p in pages] == [
        ("left", LOWER.yc),
        ("right", UPPER.yc),
    ]


def test_predictions_are_ordered_too():
    [formatted] = format_pages([_scan([UPPER, LOWER], 90, edited=False)])
    assert [p["yc"] for p in formatted["pages"]] == [LOWER.yc, UPPER.yc]
