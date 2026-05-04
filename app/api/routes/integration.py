from datetime import datetime
import logging
import os
from urllib.parse import urljoin
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
import grpc
from app.api.authz import (
    from_group_id,
    from_external_id,
    require_group_permission,
    require_task_state,
)
from app.api.setup_db import get_db
from app.api.utils import (
    format_page_data_flat,
    get_wrong_predictions,
    remove_title_from_storage,
)
from app.db.operations.api import (
    delete_title_from_db_and_storage,
    link_titles_to_group_bulk,
    set_default_title_params,
)
from app.db.schemas.title import Scan, TaskState, Title, TitleCreate
from starlette.responses import RedirectResponse
from pymongo.errors import DuplicateKeyError
from app.db.schemas.user import Permission
from app.tasks.workflows.integration_workflow import prepare_data_workflow

router = APIRouter(prefix="/integration", tags=["Integration"])
logger = logging.getLogger(__name__)

WEBAPP_URL = os.getenv("WEBAPP_FRONTEND_URL")
UPLOAD_VOLUME_PATH = os.getenv("SCANS_VOLUME_PATH")


@router.post(
    "/create",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=from_group_id)
        )
    ],
)
async def create_title(group_id: str, title_data: TitleCreate, db=Depends(get_db)):
    """Creates a new title and schedules a Hatchet workflow for it."""
    if not ObjectId.is_valid(group_id):
        raise HTTPException(400, f"ID '{group_id}' is not a valid ObjectId")

    title_data_dict = title_data.model_dump(by_alias=True)

    # Remove existing title with the same external_id, book is being rescanned
    existing_title_ids = [
        t["_id"]
        for t in await db.titles.find(
            {"external_id": title_data_dict["external_id"]}, {"_id": 1}
        ).to_list(length=None)
    ]
    if len(existing_title_ids) > 0:
        logger.info(
            f"Title with external_id {title_data_dict['external_id']} already exists, removing for rescan."
        )
        title_data_dict["_id"] = existing_title_ids[0]  # reuse existing ID
        try:
            for title_id in existing_title_ids:
                await delete_title_from_db_and_storage(str(title_id), group_id, db)
        except Exception as e:
            logger.error(f"Failed to delete title ID {title_id}: {e}")

    try:
        doc = Title.model_validate(title_data_dict)
        doc = await set_default_title_params(doc, group_id, db)
        await db.titles.insert_one(doc.model_dump(by_alias=True))

        # Create directory for scans
        os.makedirs(os.path.join(UPLOAD_VOLUME_PATH, str(doc.id)), exist_ok=True)
        # Assign to the default group
        await link_titles_to_group_bulk(
            title_ids=[ObjectId(doc.id)], group_id=ObjectId(group_id), db=db
        )
    except DuplicateKeyError:
        raise HTTPException(400, "Title with this id already exists")
    except Exception as e:
        raise HTTPException(400, f"Invalid title data: {e}")

    # Schedule task and update state
    try:
        await prepare_data_workflow.aio_run_no_wait(input=doc)
    except grpc.RpcError:
        logger.warning(
            f"gRPC timeout when scheduling workflow for title {doc.external_id}"
        )
        pass  # ignore gRPC timeout, the task will be created anyway

    await db.titles.update_one(
        {"external_id": doc.external_id},
        {"$set": {"state": TaskState.scheduled}},
    )

    logger.info(f"Scheduled workflow for title {doc.external_id} (id: {doc.id})")
    return {"state": TaskState.scheduled, "id": doc.external_id}


@router.get(
    "/{external_id}/status",
    dependencies=[
        Depends(
            require_group_permission(
                Permission.upload, group_id_provider=from_external_id
            )
        )
    ],
)
async def get_title_state(external_id: str, db=Depends(get_db)):
    """Gets the current state of a title by its ID."""
    title = await db.titles.find_one({"external_id": external_id})
    if not title:
        raise HTTPException(404, "Title not found")

    return {"state": title.get("state"), "id": external_id}


@router.get(
    "/{external_id}/open",
    dependencies=[
        Depends(
            require_task_state(
                [TaskState.ready, TaskState.user_approved], external_id_provider=True
            )
        )
    ],
)
async def open_webapp(external_id: str, db=Depends(get_db)):
    """Opens web editor with predicted pages for the given title."""
    title = await db.titles.find_one({"external_id": external_id})
    url = urljoin(WEBAPP_URL, f"book/{title['_id']}")
    logger.info(f"Redirecting to {url}")

    return RedirectResponse(url=url, status_code=301)


@router.get(
    "/{external_id}/coordinates",
    dependencies=[
        Depends(
            require_group_permission(
                Permission.upload, group_id_provider=from_external_id
            )
        )
    ],
)
async def get_coordinates(external_id: str, db=Depends(get_db)):
    """Get crop instructions for all pages."""
    title = await db.titles.find_one({"external_id": external_id})

    scans = [Scan.model_validate(scan) for scan in title.get("scans", [])]
    pages = format_page_data_flat(scans, title["filelist"])

    logger.info(f"Returning coordinates for title {external_id}, {len(pages)} pages")
    return {
        "id": external_id,
        "pages": pages,
    }


@router.post(
    "/{external_id}/complete",
    dependencies=[
        Depends(
            require_group_permission(
                Permission.upload, group_id_provider=from_external_id
            )
        ),
        Depends(
            require_task_state(
                [TaskState.ready, TaskState.user_approved], external_id_provider=True
            )
        ),
    ],
)
async def mark_completed(external_id: str, db=Depends(get_db)):
    """Mark a title as completed."""
    title = await db.titles.find_one({"external_id": external_id})
    title = Title.model_validate(title)
    if len(title.scans) == 0:
        await delete_title_from_db_and_storage(str(title.id), str(title.group_id), db)
        logger.info(f"Title {external_id} has no scans, skipped.")
        return {"state": TaskState.completed, "id": external_id}

    # Check number of errors from the coordinate prediction model
    errors = get_wrong_predictions(title.scans)

    if len(errors) / len(title.scans) > 0.1:  # more than 10% of pages were edited
        state = TaskState.retrain

    else:  # Title is correct, mark as completed, delete scans from upload volume
        state = TaskState.completed
        remove_title_from_storage(str(title.id))

    await db.titles.update_one(
        {"external_id": external_id},
        {
            "$set": {
                "state": state,
                "modified_at": datetime.now(),
            }
        },
    )
    logger.info(
        f"Title {external_id} marked as {state}. Title contains {len(errors)}/{len(title.scans)} errors."
    )
    return {"state": state, "id": external_id}
