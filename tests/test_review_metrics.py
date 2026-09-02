from bson import ObjectId

from app.core.review_metrics import bbox, compute_review_stats, iou, scan_delta
from app.db.schemas.title import Page, Scan, Settings, Title


def page(xc, yc, w, h, angle=0.0) -> Page:
    return Page(_id=ObjectId(), xc=xc, yc=yc, width=w, height=h, angle=angle)


def scan(predicted, edited=None, orientation=0) -> Scan:
    return Scan(
        _id=ObjectId(),
        filename="f.jpg",
        scan_name="f",
        predicted_pages=predicted,
        user_edited_pages=edited,
        orientation=orientation,
    )


LEFT = page(0.25, 0.5, 0.4, 0.9)
RIGHT = page(0.75, 0.5, 0.4, 0.9)


def test_identical_pages_are_unchanged():
    d = scan_delta([LEFT], [page(0.25, 0.5, 0.4, 0.9)])
    assert d.matched == 1
    assert d.iou_sum == 1.0
    assert d.center_shift_sum == 0.0
    assert d.changed is False


def test_shifted_page_reports_center_shift_and_lower_iou():
    d = scan_delta([LEFT], [page(0.30, 0.5, 0.4, 0.9)])
    assert d.matched == 1
    assert abs(d.center_shift_sum - 0.05) < 1e-9
    assert d.iou_sum < 1.0
    assert d.changed is True


def test_removed_page_counts_as_removed_and_keeps_other_match():
    d = scan_delta([LEFT, RIGHT], [page(0.25, 0.5, 0.4, 0.9)])
    assert d.matched == 1
    assert d.pages_removed == 1
    assert d.pages_added == 0
    assert d.changed is True


def test_page_added_on_the_left_does_not_shift_existing_match():
    # Sorting by xc would pair the new left page with the old one; IoU pairing
    # keeps the original page matched to itself.
    new_left = page(0.1, 0.5, 0.15, 0.9)
    d = scan_delta([RIGHT], [new_left, page(0.75, 0.5, 0.4, 0.9)])
    assert d.matched == 1
    assert d.pages_added == 1
    assert d.iou_sum == 1.0
    assert d.center_shift_sum == 0.0


def test_angle_delta_is_absolute_difference():
    d = scan_delta(
        [page(0.5, 0.5, 0.8, 0.9, angle=2.0)], [page(0.5, 0.5, 0.8, 0.9, angle=4.5)]
    )
    assert abs(d.angle_delta_sum - 2.5) < 1e-9
    assert d.changed is True


def test_iou_of_disjoint_boxes_is_zero():
    assert iou(bbox(LEFT), bbox(RIGHT)) == 0.0


def test_compute_review_stats_aggregates_title():
    scans = [scan([LEFT, RIGHT]) for _ in range(8)]
    scans.append(
        scan([LEFT, RIGHT], [page(0.25, 0.5, 0.4, 0.9), page(0.75, 0.5, 0.4, 0.9)])
    )
    scans.append(scan([LEFT, RIGHT], [page(0.30, 0.5, 0.4, 0.9)], orientation=90))
    title = Title(scans=scans, settings=Settings(crop_model="m1", rotation_model="r1"))

    stats = compute_review_stats(title)

    assert stats.scans_total == 10
    assert stats.scans_edited == 2
    assert stats.scans_changed == 1  # the identical re-save is not a real change
    assert stats.edit_ratio == 0.2
    assert stats.pages_predicted == 20
    assert stats.pages_edited == 3
    assert stats.pages_removed == 1
    assert stats.pages_added == 0
    assert stats.pairs_matched == 3
    assert stats.orientation_changed == 1
    assert stats.mean_iou is not None and stats.mean_iou < 1.0
    assert stats.crop_model == "m1"
    assert stats.rotation_model == "r1"


def test_compute_review_stats_without_edits_has_no_means():
    title = Title(scans=[scan([LEFT, RIGHT]), scan([LEFT])])
    stats = compute_review_stats(title)
    assert stats.scans_edited == 0
    assert stats.edit_ratio == 0.0
    assert stats.mean_iou is None
    assert stats.mean_center_shift is None
    assert stats.crop_model is None
