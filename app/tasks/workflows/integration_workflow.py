# > Create a workflow

from datetime import timedelta
import logging
from app.tasks.workflows.smartcrop_workflow import autocrop_workflow

from app.api.utils import save_scan_to_storage
from app.db.operations.hatchet import (
    db_replace_scans,
    db_update_task_state,
)
from app.db.schemas.title import TaskState, Title
from app.tasks.hatchet_client import hatchet

from hatchet_sdk import (
    Context,
    EmptyModel,
)

from app.tasks.workflows.smartcrop_workflow import _ensure_db


logger = logging.getLogger(__name__)
prepare_data_workflow = hatchet.workflow(name="prepare-data-workflow")


@prepare_data_workflow.task(execution_timeout=timedelta(minutes=10))
def upload_scans(input: Title, ctx: Context):
    """Crops images in the input folder using the specified method."""
    ctx.log(f"Starting data preparation task for title {input.id}")

    # Save all scan images
    input = Title.model_validate(ctx.workflow_input, by_name=True)
    for file in input.filelist:
        logger.info(f"Saving scan {file} for title {input.external_id}")
        # Create scan object
        scan = save_scan_to_storage(str(input.id), file, file)
        logger.info(scan)
        input.scans.append(scan)

    db_replace_scans(input.id, input.scans, _ensure_db())

    # Run main task
    autocrop_workflow.run_no_wait(input=input)


@prepare_data_workflow.on_failure_task()
def mark_as_failed(input: EmptyModel, ctx: Context):
    """Handles errors by updating the task state to failed."""
    title_id = ctx.workflow_input["id"]
    db_update_task_state(title_id, TaskState.failed, _ensure_db())
    ctx.log("Workflow failed, updated task state to failed.")
