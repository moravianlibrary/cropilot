import math
import logging
from app.core.rotate_net.dataset import PageAngleDataset
from app.core.rotate_net.network import AngleDegModel, predict_angles
from torch.utils.data import DataLoader
from app.db.schemas.title import Anomaly, Page, Scan

logger = logging.getLogger(__name__)
logger.propagate = True

rotation_model = None


def _ensure_rotation_model():
    global rotation_model
    if rotation_model is None:
        rotation_model = AngleDegModel(model="models/rotate-300e-best.pth")
    return rotation_model


def rotate_pages(
    scan_results: list["Scan"], model=_ensure_rotation_model()
) -> list["Scan"]:
    """Predict rotation angles for all pages and update the Scan results.

    Args:
        scan_results (list[Scan]): List of Scan objects with predicted pages.
        model (AngleDegModel): Preloaded rotation model.
    Returns:
        list[Scan]: Updated Scan objects with predicted angles.
    """
    page_bboxes = [
        (res.filename, bbox.xc, bbox.yc, bbox.width, bbox.height)
        for res in scan_results
        for bbox in res.predicted_pages
    ]
    images = [b[0] for b in page_bboxes]
    coordinates = [b[1:] for b in page_bboxes]

    # Create dataset for rotation prediction
    df = PageAngleDataset(
        image_paths=images,
        image_bboxes=coordinates,
        is_train=False,
        image_size=640,
        angle_max=10.0,
    )
    loader = DataLoader(
        df, batch_size=16, shuffle=False, num_workers=0, pin_memory=True
    )
    preds = predict_angles(model, loader)

    # Save predicted angles back to scan results
    idx = 0
    for res in scan_results:
        for bbox in res.predicted_pages:
            bbox.angle = -preds[idx]
            idx += 1

        # Post-process: fix rotation errors and resize bboxes
        # res.predicted_pages = autofix_rotation_errors(res.predicted_pages, res.filename)

    return scan_results


def resize_bbox_ratio_by_angle(w_a, h_a, angle_deg):
    """Calculates new bounding box size after rotation by a given angle. Tightens the box dimensions around text.

    Args:
        w_a (float): Original width of the bounding box.
        h_a (float): Original height of the bounding box.
        angle_deg (float): Rotation angle in degrees.
    Returns:
        tuple: New width and height of the bounding box after fixing the rotation.
    """
    a = math.radians(angle_deg)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    denom = c**2 - s**2
    w = (c * w_a - s * h_a) / denom
    h = (-s * w_a + c * h_a) / denom

    logger.debug(
        f"Rescaled dimensions for angle {angle_deg:.2f}°: ({w_a:.2f}, {h_a:.2f}) => ({w:.2f}, {h:.2f})"
    )
    return w, h


def autofix_rotation_errors(
    pages: list["Page"], filename: str, model=_ensure_rotation_model()
) -> list["Page"]:
    """Autofix angles for multi-page scans with conflicting angles.
    Works by rerunning angle prediction with a larger bounding box (covering 50% of the image).

    Args:
        pages (list[Page]): List of detected pages.
        filename (str): Image filename.
        model (AngleDegModel): Preloaded rotation model.
    Returns:
        list[Page]: List of detected pages with potentially updated angles.
    """
    # Only fix two-page scans where angle difference is larger than 3°
    if len(pages) != 2 or abs(pages[0].angle - pages[1].angle) < 3.0:
        return pages

    if abs(pages[0].angle) > abs(pages[1].angle):
        rerun_idx = 0
        new_xc, new_yc, new_w, new_h = (0.25, 0.5, 0.5, 1)
    else:
        rerun_idx = 1
        new_xc, new_yc, new_w, new_h = (0.75, 0.5, 0.5, 1)

    dataloader = DataLoader(
        PageAngleDataset(
            image_paths=[filename],
            image_bboxes=[(new_xc, new_yc, new_w, new_h)],
            is_train=False,
            image_size=640,
            angle_max=10.0,
        ),
        batch_size=1,
        shuffle=False,
        num_workers=0,
        pin_memory=True,
    )

    preds = predict_angles(model, dataloader)

    pages[rerun_idx].angle = -preds[0]
    pages[rerun_idx].flags.append(Anomaly.low_confidence)

    logger.info(
        f"Autofixed rotation angle for page {rerun_idx} to {pages[rerun_idx].angle:.2f}°"
    )
    return pages
