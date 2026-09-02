"""Pure functions comparing ML page predictions with user edits.

All coordinates are normalized (0..1) as stored in ``Page``. IoU is computed on
axis-aligned boxes and ignores ``angle``: RotateNET predictions are capped at
about 10 degrees, so the approximation error is small and the angle change is
reported separately as ``angle_delta``.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.db.schemas.title import Page, ReviewStats, Title

# A matched pair counts as "changed" when any of these thresholds is exceeded.
IOU_CHANGED_BELOW = 0.99
CENTER_SHIFT_CHANGED_ABOVE = 0.005
ANGLE_CHANGED_ABOVE = 0.5

Box = tuple[float, float, float, float]


def bbox(page: Page) -> Box:
    """Axis-aligned (x1, y1, x2, y2) from center + size."""
    hw, hh = page.width / 2, page.height / 2
    return (page.xc - hw, page.yc - hh, page.xc + hw, page.yc + hh)


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def center_distance(a: Page, b: Page) -> float:
    return ((a.xc - b.xc) ** 2 + (a.yc - b.yc) ** 2) ** 0.5


def match_pages(
    predicted: list[Page], edited: list[Page]
) -> tuple[list[tuple[Page, Page]], list[Page], list[Page]]:
    """Greedy pairing by best IoU; falls back to nearest center when IoU is 0.

    Sorting by ``xc`` would mislabel a page added on the left as "the left page
    moved"; IoU-based pairing keeps the surviving page matched to itself.
    Returns (pairs, unmatched_predicted, unmatched_edited).
    """
    remaining_pred = list(predicted)
    remaining_edit = list(edited)
    pairs: list[tuple[Page, Page]] = []

    while remaining_pred and remaining_edit:
        best: tuple[float, float, int, int] | None = None
        for i, p in enumerate(remaining_pred):
            bp = bbox(p)
            for j, e in enumerate(remaining_edit):
                score = iou(bp, bbox(e))
                dist = center_distance(p, e)
                # Higher IoU wins; among zero-IoU candidates the nearest wins.
                key = (score, -dist, i, j)
                if best is None or key[:2] > best[:2]:
                    best = key
        assert best is not None
        _, _, i, j = best
        pairs.append((remaining_pred.pop(i), remaining_edit.pop(j)))

    return pairs, remaining_pred, remaining_edit


@dataclass
class ScanDelta:
    matched: int = 0
    iou_sum: float = 0.0
    center_shift_sum: float = 0.0
    width_delta_sum: float = 0.0
    height_delta_sum: float = 0.0
    angle_delta_sum: float = 0.0
    pages_added: int = 0
    pages_removed: int = 0
    changed: bool = False
    pairs: list[tuple[Page, Page]] = field(default_factory=list)


def scan_delta(predicted: list[Page], edited: list[Page]) -> ScanDelta:
    """Compare one scan's predicted pages with its user-edited pages."""
    pairs, unmatched_pred, unmatched_edit = match_pages(predicted, edited)
    d = ScanDelta(pairs=pairs)
    d.pages_removed = len(unmatched_pred)
    d.pages_added = len(unmatched_edit)
    d.changed = bool(unmatched_pred or unmatched_edit)

    for p, e in pairs:
        pair_iou = iou(bbox(p), bbox(e))
        shift = center_distance(p, e)
        angle_delta = abs(e.angle - p.angle)
        d.matched += 1
        d.iou_sum += pair_iou
        d.center_shift_sum += shift
        d.width_delta_sum += e.width - p.width
        d.height_delta_sum += e.height - p.height
        d.angle_delta_sum += angle_delta
        if (
            pair_iou < IOU_CHANGED_BELOW
            or shift > CENTER_SHIFT_CHANGED_ABOVE
            or angle_delta > ANGLE_CHANGED_ABOVE
        ):
            d.changed = True
    return d


def compute_review_stats(title: Title, now: datetime | None = None) -> ReviewStats:
    """Aggregate per-scan deltas into a title-level ``ReviewStats``.

    ``scans_edited`` uses the same predicate as ``get_wrong_predictions``
    (``user_edited_pages is not None``) so the integration retrain rule keeps
    its exact historical behaviour.
    """
    scans_total = len(title.scans)
    scans_edited = 0
    scans_changed = 0
    pages_predicted = 0
    pages_edited = 0
    pages_added = 0
    pages_removed = 0
    matched = 0
    iou_sum = shift_sum = w_sum = h_sum = angle_sum = 0.0
    orientation_changed = 0

    for scan in title.scans:
        pages_predicted += len(scan.predicted_pages)
        if scan.orientation != 0:
            orientation_changed += 1
        if scan.user_edited_pages is None:
            continue
        scans_edited += 1
        pages_edited += len(scan.user_edited_pages)
        d = scan_delta(scan.predicted_pages, scan.user_edited_pages)
        if d.changed:
            scans_changed += 1
        pages_added += d.pages_added
        pages_removed += d.pages_removed
        matched += d.matched
        iou_sum += d.iou_sum
        shift_sum += d.center_shift_sum
        w_sum += d.width_delta_sum
        h_sum += d.height_delta_sum
        angle_sum += d.angle_delta_sum

    def mean(total: float) -> float | None:
        return round(total / matched, 4) if matched else None

    return ReviewStats(
        scans_total=scans_total,
        scans_edited=scans_edited,
        scans_changed=scans_changed,
        edit_ratio=round(scans_edited / scans_total, 4) if scans_total else 0.0,
        pages_predicted=pages_predicted,
        pages_edited=pages_edited,
        pages_added=pages_added,
        pages_removed=pages_removed,
        pairs_matched=matched,
        mean_iou=mean(iou_sum),
        mean_center_shift=mean(shift_sum),
        mean_width_delta=mean(w_sum),
        mean_height_delta=mean(h_sum),
        mean_angle_delta=mean(angle_sum),
        orientation_changed=orientation_changed,
        crop_model=title.settings.crop_model if title.settings else None,
        rotation_model=title.settings.rotation_model if title.settings else None,
        computed_at=now or datetime.now(),
    )
