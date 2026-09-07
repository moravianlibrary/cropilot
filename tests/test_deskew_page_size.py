"""AABB -> upright page size inversion applied after rotation prediction."""

import math

import pytest

from app.core.utils import deskew_page_size, upright_size_from_aabb
from app.db.schemas.title import Page


def aabb(w, h, angle_deg):
    """Same forward formula the cropilot-utils trainer uses for YOLO labels."""
    r = math.radians(angle_deg)
    return (
        abs(w * math.cos(r)) + abs(h * math.sin(r)),
        abs(w * math.sin(r)) + abs(h * math.cos(r)),
    )


@pytest.mark.parametrize("angle", [-9.5, -7, -2, 0.5, 3, 7, 12])
@pytest.mark.parametrize("size", [(1600, 1100), (900, 1400), (1200, 1200)])
def test_upright_size_round_trips_through_aabb(size, angle):
    w, h = size
    aw, ah = aabb(w, h, angle)
    assert aw >= w and ah >= h  # the AABB never shrinks the page

    rw, rh = upright_size_from_aabb(aw, ah, angle)

    assert rw == pytest.approx(w, abs=1e-6)
    assert rh == pytest.approx(h, abs=1e-6)


def test_zero_angle_is_identity():
    assert upright_size_from_aabb(800.0, 550.0, 0.0) == (800.0, 550.0)


def test_degenerate_angles_leave_box_unchanged():
    assert upright_size_from_aabb(800.0, 550.0, 45.0) == (800.0, 550.0)
    assert upright_size_from_aabb(800.0, 550.0, 60.0) == (800.0, 550.0)


def test_inconsistent_box_leaves_size_unchanged():
    # A box clipped at the image edge can be narrower than any tilted page of
    # that height allows; the inversion would go negative -> keep the AABB.
    assert upright_size_from_aabb(100.0, 2000.0, 10.0) == (100.0, 2000.0)


def test_deskew_page_size_uses_image_aspect_and_keeps_center():
    img_w, img_h = 2000, 1500
    page_w, page_h, angle = 1600, 825, 7.0  # ~ the postcard case
    aw, ah = aabb(page_w, page_h, angle)
    page = Page(xc=0.5, yc=0.5, width=aw / img_w, height=ah / img_h, angle=angle)

    deskew_page_size(page, img_w, img_h)

    assert (page.xc, page.yc, page.angle) == (0.5, 0.5, angle)
    assert page.width == pytest.approx(page_w / img_w, abs=1e-4)
    assert page.height == pytest.approx(page_h / img_h, abs=1e-4)


def test_deskew_page_size_skips_pages_without_angle():
    page = Page(xc=0.5, yc=0.5, width=0.86, height=0.64, angle=0)
    deskew_page_size(page, 2000, 1500)
    assert (page.width, page.height) == (0.86, 0.64)
