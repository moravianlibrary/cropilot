import logging
import os
import cv2
from ultralytics import YOLO

from app.db.schemas.title import Page, Scan
from app.core.utils import (
    add_margin,
    bbox_from_image_contours,
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
        path = os.path.join(os.getenv("MODELS_VOLUME_PATH"), f"{name}.pt")
        crop_model[name] = YOLO(path, task="detect")
    return crop_model[name]


def crop_images_outer(filelist: list[str]) -> list[Scan]:
    """Crops images in the input folder by finding the largest contour.

    Args:
        input_folder (str): Path to the folder containing images.
    Returns:
        List[Scan]: List of detected pages with bounding boxes.
    """
    results = []
    for file in filelist:
        # Create a new Scan object for each file
        scan = Scan(filename=file)

        # Generate outer bounding box
        image = cv2.imread(file)
        w, h = image.shape[1], image.shape[0]
        outer_box = bbox_from_image_contours(image)
        outer_box = add_margin(outer_box, margin=(w * 0.01, h * 0.01))  # Add 1% margin

        # Cap to image size
        outer_box = [
            max(0, int(outer_box[0])),
            max(0, int(outer_box[1])),
            min(w, int(outer_box[2])),
            min(h, int(outer_box[3])),
        ]

        scan.predicted_pages.append(
            Page(
                xc=(outer_box[0] + outer_box[2]) / 2 / w,
                yc=(outer_box[1] + outer_box[3]) / 2 / h,
                width=(outer_box[2] - outer_box[0]) / w,
                height=(outer_box[3] - outer_box[1]) / h,
                confidence=1.0,
            )
        )
        results.append(scan)

        logger.info(f"Cropped image {file} to box: {outer_box}")
    return results


def crop_images(
    filelist: list[str], crop_model: str, batch_size: int = 16
) -> list[Scan]:
    """Crops images in the input folder using a pretrained YOLO model.

    Args:
        input_folder (str): Path to the folder containing images.
        batch_size (int): Batch size.
    Returns:
        List[Scan]: List of detected pages with bounding boxes.
    """
    model = _ensure_crop_model(crop_model)
    results = []
    for i in range(0, len(filelist), batch_size):
        # Predict per batch
        batch = filelist[i : i + batch_size]
        batch_result = model.predict(
            batch, conf=0.1, iou=0, max_det=2, agnostic_nms=True
        )

        for yolo_result in batch_result:
            # Create a new Scan object for each file
            scan = Scan(filename=yolo_result.path)

            # Process detected boxes
            for box in yolo_result.boxes:
                w_orig, h_orig = np.array(
                    yolo_result.orig_img.shape[1::-1], dtype=np.float32
                )
                if box:
                    logger.debug(
                        f"Cropped image {yolo_result.path} to box: {box.xyxy[0].cpu().numpy()}"
                    )
                    xc, yc, w, h = box.xywh[0].cpu().numpy()

                    # Normalize by dividing by image width and height
                    xc /= w_orig
                    yc /= h_orig
                    w /= w_orig
                    h /= h_orig
                else:
                    xc, yc, w, h = (w_orig / 2, h_orig / 2, w_orig, h_orig)

                scan.predicted_pages.append(
                    Page(
                        xc=xc,
                        yc=yc,
                        width=w,
                        height=h,
                        confidence=box.conf.item(),
                    )
                )
            # Merge overlapping pages
            scan = merge_overlaps(scan)
            results.append(scan)

    return results
