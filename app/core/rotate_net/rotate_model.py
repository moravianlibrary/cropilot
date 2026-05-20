import logging
import os
from app.core.rotate_net.dataset import PageAngleDataset
from app.core.rotate_net.network import AngleDegModel, predict_angles
from torch.utils.data import DataLoader
from app.db.schemas.title import Scan

logger = logging.getLogger(__name__)
logger.propagate = True

rotation_model = {}


def _ensure_rotation_model(name: str):
    global rotation_model
    if name not in rotation_model:
        # Initialize the model and store it in the global variable
        path = os.path.join(os.getenv("MODELS_VOLUME_PATH"), "rotation_model", f"{name}.pth")
        if not os.path.exists(path):
            logger.warning(f"Rotation model '{name}' not found at '{path}', falling back to default.")
            path = os.path.join(os.getenv("MODELS_VOLUME_PATH"), "rotation_model", "text.pth")
        
        rotation_model[name] = AngleDegModel(model=path)
    return rotation_model[name]


def rotate_pages(
    scan_results: list["Scan"],
    rotation_model: str,
) -> list["Scan"]:
    """Predict rotation angles for all pages and update the Scan results.

    Args:
        scan_results (list[Scan]): List of Scan objects with predicted pages.
        model (AngleDegModel): Preloaded rotation model.
    Returns:
        list[Scan]: Updated Scan objects with predicted angles.
    """
    model = _ensure_rotation_model(rotation_model)
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
            bbox.angle = round(float(-preds[idx]), 2)
            idx += 1

    return scan_results
