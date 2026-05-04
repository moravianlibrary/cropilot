import logging
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
