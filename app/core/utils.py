import logging
import math

import numpy as np

from app.db.schemas.title import Scan

logger = logging.getLogger(__name__)


def denormalize_bbox(
    bbox: tuple[float, float, float, float], img_w: int, img_h: int
) -> tuple[int, int, int, int]:
    """Denormalizes a bounding box from relative coordinates to absolute pixel values."""
    return (
        int(bbox[0] * img_w),
        int(bbox[1] * img_h),
        int(bbox[2] * img_w),
        int(bbox[3] * img_h),
    )


def cxywh_to_xyxy(xc: int, yc: int, w: int, h: int) -> tuple[int, int, int, int]:
    """Converts bounding box from center x, center y, width, height to x1, y1, x2, y2 format."""
    x1 = int(xc - w / 2)
    y1 = int(yc - h / 2)
    x2 = int(xc + w / 2)
    y2 = int(yc + h / 2)
    return x1, y1, x2, y2


def cxywh_norm_to_xyxy(
    xc: float, yc: float, w: float, h: float
) -> tuple[float, float, float, float]:
    """Converts bounding box from center x, center y, width, height to x1, y1, x2, y2 format."""
    x1 = xc - w / 2
    y1 = yc - h / 2
    x2 = xc + w / 2
    y2 = yc + h / 2
    return round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)


def bbox_intersection(
    box: np.ndarray, intersect_with_box: np.ndarray
) -> np.ndarray | None:
    """Creates an intersection box between two bounding boxes.

    Args:
        box (numpy.ndarray): Bounding box coordinates [x1, y1, x2, y2].
        intersect_with_box (numpy.ndarray): Bounding box to intersect with [x1, y1, x2, y2].
    Returns:
        numpy.ndarray | None: Intersection bounding box or None if no intersection.
    """
    x1 = max(box[0], intersect_with_box[0])
    y1 = max(box[1], intersect_with_box[1])
    x2 = min(box[2], intersect_with_box[2])
    y2 = min(box[3], intersect_with_box[3])
    if x1 >= x2 or y1 >= y2:
        return None  # No intersection
    return np.array([x1, y1, x2, y2], np.int32)


def bbox_size(box: np.ndarray) -> float:
    """Calculates the size of a bounding box.

    Args:
        box (numpy.ndarray): Bounding box coordinates [x1, y1, x2, y2].
    Returns:
        float: area of the bounding box.
    """
    return (box[2] - box[0]) * (box[3] - box[1]) if box is not None else 0


def merge_overlaps(scan: Scan) -> Scan:
    """Removes overlapping pages in a Scan object.

    Args:
        scan (Scan): Scan object with predicted pages.
    Returns:
        Scan: Updated Scan object with merged pages.
    """
    if not len(scan.predicted_pages) == 2:
        return scan  # Only merge if there are exactly two pages

    box1 = scan.predicted_pages[0]
    box2 = scan.predicted_pages[1]
    x1_1, y1_1, x2_1, y2_1 = cxywh_to_xyxy(
        box1.xc * 1000, box1.yc * 1000, box1.width * 1000, box1.height * 1000
    )
    box1 = np.array([x1_1, y1_1, x2_1, y2_1])

    x1_2, y1_2, x2_2, y2_2 = cxywh_to_xyxy(
        box2.xc * 1000, box2.yc * 1000, box2.width * 1000, box2.height * 1000
    )
    box2 = np.array([x1_2, y1_2, x2_2, y2_2])

    intersection = bbox_intersection(box1, box2)

    # if intersection is larger than 80% of the smaller box, drop
    if intersection is not None:
        if bbox_size(intersection) / min(bbox_size(box1), bbox_size(box2)) > 0.8:
            if bbox_size(box1) >= bbox_size(box2):
                scan.predicted_pages = [scan.predicted_pages[0]]
            else:
                scan.predicted_pages = [scan.predicted_pages[1]]
    return scan


def upright_size_from_aabb(
    aabb_w: float, aabb_h: float, angle_deg: float
) -> tuple[float, float]:
    """Recovers the upright page size from its axis-aligned bounding box.

    The YOLO position model sees un-deskewed scans, so its box is the
    axis-aligned bounding box (AABB) of the tilted page::

        W = w*cos(a) + h*sin(a)
        H = w*sin(a) + h*cos(a)

    Once the rotation model has predicted ``a`` this can be inverted, so the
    stored page rectangle matches the page and not its (larger) AABB. All sizes
    are in the same unit (pixels). Angles of 45 degrees or more, or a result
    that is not a valid rectangle (box clipped at the image edge, noisy angle),
    return the input unchanged.
    """
    a = abs(math.radians(angle_deg))
    c, s = math.cos(a), math.sin(a)
    det = c * c - s * s  # cos(2a)
    if det <= 1e-6:
        return aabb_w, aabb_h
    w = (aabb_w * c - aabb_h * s) / det
    h = (aabb_h * c - aabb_w * s) / det
    if w <= 0 or h <= 0:
        return aabb_w, aabb_h
    return w, h


def deskew_page_size(page, img_w: int, img_h: int) -> None:
    """Shrinks a predicted page from AABB to upright size in place.

    ``page.width`` / ``page.height`` are normalized to the image, so the
    inversion runs in pixels and the result is renormalized. The center and the
    angle are unchanged.
    """
    if not page.angle or img_w <= 0 or img_h <= 0:
        return
    w_px, h_px = upright_size_from_aabb(
        page.width * img_w, page.height * img_h, page.angle
    )
    page.width = round(w_px / img_w, 4)
    page.height = round(h_px / img_h, 4)
