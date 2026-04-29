# > Create a workflow

from datetime import timedelta
import logging

import certifi
from pymongo import MongoClient
from app.core.rotate_net.rotate_model import rotate_pages
from app.db.operations.hatchet import (
    db_get_state,
    db_replace_scans,
    db_update_task_state,
)
from app.db.schemas.title import TaskState, Title
from app.core.anomalies import (
    flag_dimensions_anomalies,
    flag_low_confidence,
    flag_missing_pages,
    flag_prediction_errors,
    flag_prediction_overlaps,
)
from app.core.yolo_crop.crop_model import crop_images
from app.tasks.hatchet_client import hatchet
from app.deps import settings_db

from hatchet_sdk import (
    Context,
    EmptyModel,
)

_client = None
_db = None


def _ensure_db():
    global _client, _db
    if _db is None:
        if settings_db.tls_enabled:
            _client = MongoClient(settings_db.mongodb_uri, tlsCAFile=certifi.where())
        else:
            _client = MongoClient(settings_db.mongodb_uri)
        _db = _client.get_database(settings_db.mongodb_db)
    return _db


logger = logging.getLogger(__name__)
autocrop_workflow = hatchet.workflow(name="autocrop-title-workflow")


@autocrop_workflow.task(execution_timeout=timedelta(minutes=10))
def crop(input: Title, ctx: Context):
    """Crops images in the input folder using the specified method."""
    ctx.log(f"Starting crop task for title {input.id}")

    output = Title.model_validate(ctx.workflow_input, by_name=True)

    current_state = db_get_state(input.id, _ensure_db())
    if current_state != TaskState.scheduled:
        ctx.log(f"Input {input.id} is not in a valid state.")
        ctx.aio_cancel()
        return

    db_update_task_state(input.id, TaskState.in_progress, _ensure_db())
    output.state = TaskState.in_progress

    output.scans = crop_images(output.scans, output.settings.crop_model)

    return output


@autocrop_workflow.task(parents=[crop], execution_timeout=timedelta(minutes=10))
def rotate(input: EmptyModel, ctx: Context):
    """Rotates images based on detected bounding boxes."""
    ctx.log(f"Starting rotate task for title {ctx.workflow_input['id']}")

    output = ctx.task_output(crop)
    output = Title.model_validate(output, by_name=True)

    output.scans = rotate_pages(output.scans, output.settings.rotation_model)
    return output


@autocrop_workflow.task(parents=[rotate], execution_timeout=timedelta(minutes=5))
def detect_anomalies(input: EmptyModel, ctx: Context):
    """Detects potential mistakes in the processed images."""
    output = ctx.task_output(rotate)
    output = Title.model_validate(output, by_name=True)

    output.scans = flag_missing_pages(output.scans)
    output.scans = flag_low_confidence(output.scans)
    output.scans = flag_dimensions_anomalies(output.scans)
    output.scans = flag_prediction_errors(output.scans)
    output.scans = flag_prediction_overlaps(output.scans)

    db_replace_scans(output.id, output.scans, _ensure_db())
    db_update_task_state(output.id, TaskState.ready, _ensure_db())
    output.state = TaskState.ready

    # Serialize Pydantic objects
    ctx.log(f"Processed {len(output.scans)} scans and assigned anomalies")
    return output


@autocrop_workflow.on_failure_task()
def mark_as_failed(input: EmptyModel, ctx: Context):
    """Handles errors by updating the task state to failed."""
    title_id = ctx.workflow_input["id"]
    db_update_task_state(title_id, TaskState.failed, _ensure_db())
    ctx.log("Workflow failed, updated task state to failed.")
