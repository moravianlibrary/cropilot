import logging
import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from app.api.authz import in_any_group, require_group_permission
from app.db.schemas.user import Permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["Models"])

CROP_MODEL_PATH = os.path.join(os.environ.get("MODELS_VOLUME_PATH"), "crop_model")


@router.get("")
async def list_models():
    models = os.listdir(CROP_MODEL_PATH)
    # remove .pt extension
    models = [model[:-3] for model in models if model.endswith(".pt")]
    models_sorted = [models.pop(models.index("default"))] + sorted(models)
    logger.info(f"Listed {len(models_sorted)} models: {models_sorted}")
    return {"available_models": models_sorted}


@router.post(
    "",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=in_any_group)
        )
    ],
)
async def upload_model(file: UploadFile):
    # Save the uploaded model file
    if not file.filename.endswith(".pt"):
        raise HTTPException(400, "Invalid file")
    model_path = os.path.join(CROP_MODEL_PATH, file.filename)
    with open(model_path, "wb") as f:
        f.write(await file.read())
    logger.info(f"Uploaded model: {file.filename}")
    return {"filename": file.filename}


@router.delete(
    "/{model_name}",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=in_any_group)
        )
    ],
)
async def delete_model(model_name: str):
    model_path = os.path.join(CROP_MODEL_PATH, f"{model_name}.pt")
    if not os.path.exists(model_path):
        raise HTTPException(404, "Model not found")
    os.remove(model_path)
    logger.info(f"Deleted model: {model_name}")
    return {"detail": "Model deleted"}
