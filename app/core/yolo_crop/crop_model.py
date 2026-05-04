import logging
import os
from ultralytics import YOLO

from app.db.schemas.title import Page, Scan
from app.core.utils import (
    merge_overlaps,
)
import numpy as np

logger = logging.getLogger(__name__)
logger.propagate = True


crop_model = {}


def _ensure_crop_model(name: str) -> YOLO:
    global crop_model
    if name not in crop_model:
        # Initialize the YOLO model and store it in the global variable
        path = os.path.join(os.getenv("MODELS_VOLUME_PATH"), "crop_model", f"{name}.pt")
        crop_model[name] = YOLO(path, task="detect")
    return crop_model[name]


def crop_images(scans: list[Scan], crop_model: str, batch_size: int = 16) -> list[Scan]:
    """Crops images in the input folder using a pretrained YOLO model.

    Args:
        input_folder (str): Path to the folder containing images.
        batch_size (int): Batch size.
    Returns:
        List[Scan]: List of detected pages with bounding boxes.
    """
    model = _ensure_crop_model(crop_model)
    filelist = [scan.filename for scan in scans]
    scan_idx = 0
    for i in range(0, len(filelist), batch_size):
        # Predict per batch
        batch = filelist[i : i + batch_size]
        batch_result = model.predict(
            batch, conf=0.1, iou=0, max_det=2, agnostic_nms=True
        )

        for yolo_result in batch_result:
            # Process detected boxes
            for box in yolo_result.boxes:
                w_orig, h_orig = np.array(
                    yolo_result.orig_img.shape[1::-1], dtype=np.float32
                )

                xc, yc, w, h = box.xywh[0].cpu().numpy()
                # Normalize by dividing by image width and height
                xc /= w_orig
                yc /= h_orig
                w /= w_orig
                h /= h_orig

                scans[scan_idx].predicted_pages.append(
                    Page(
                        xc=xc,
                        yc=yc,
                        width=w,
                        height=h,
                        confidence=box.conf.item(),
                    )
                )
            # Merge overlapping pages
            scans[scan_idx] = merge_overlaps(scans[scan_idx])
            scan_idx += 1

    return scans
