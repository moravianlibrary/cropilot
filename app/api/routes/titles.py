from datetime import datetime
import logging
import os
from typing import Annotated
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from app.api.limiter import limiter
from app.api.authn import get_current_user
from app.api.authz import (
    from_group_id,
    from_title_id,
    require_group_permission, 
    require_task_state,
)
from app.api.setup_db import get_db
from app.api.utils import (
    format_page_data_list,
    format_predicted,
    resize_image,
    save_scan_to_storage,
    sniff_media_type,
)
from app.db.operations.api import (
    link_titles_to_group_bulk,
    set_default_title_params,
    delete_title_from_db_and_storage,
)
from app.db.schemas.title import (
    Scan,
    ScanUpdate,
    TaskState,
    Title,
    TitleCreate,
    TitleUpdate,
)
from app.db.schemas.user import Permission, User
from app.tasks.workflows.smartcrop_workflow import autocrop_workflow


UPLOAD_VOLUME_PATH = os.getenv("SCANS_VOLUME_PATH")
router = APIRouter(prefix="", tags=["Books"])
logger = logging.getLogger(__name__)


@limiter.limit("60/minute;600/hour")
@router.post(
    "/create",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=from_group_id)
        )
    ],
)
async def create_title(
    request: Request,
    group_id: str,
    title_data: TitleCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
):
    """Creates new title object with an empty list of files. Prepares a directory in volume storage.

    Returns:
        dict: Created title ID.
    """
    if not ObjectId.is_valid(group_id):
        raise HTTPException(400, f"ID '{group_id}' is not a valid ObjectId")

    try:
        # Create title entry in DB
        created_title = title_data.model_dump(by_alias=True)
        doc = Title.model_validate(created_title)
        doc = await set_default_title_params(doc, group_id, db)
        doc.filelist = []
        doc.modified_by = current_user.email
        result = await db.titles.insert_one(doc.model_dump(by_alias=True))
    except Exception as e:
        logger.error(f"Failed to create title: {e}")
        raise HTTPException(400, f"Invalid title data: {e}")

    try:
        # Create directory for scans
        os.makedirs(
            os.path.join(UPLOAD_VOLUME_PATH, str(result.inserted_id)), exist_ok=True
        )
        # Link title to group
        await link_titles_to_group_bulk(
            title_ids=[result.inserted_id], group_id=ObjectId(group_id), db=db
        )
    except Exception as e:
        logger.error(f"Failed to create title: {e}")
        await delete_title_from_db_and_storage(str(result.inserted_id), db)
        raise HTTPException(400, f"Invalid title data: {e}")
    logger.info(f"Created new title with id: {result.inserted_id}")
    return {"id": str(result.inserted_id)}


@limiter.limit("2000/minute")
@router.post(
    "/{title_id}/upload-scan",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=from_title_id)
        ),
        Depends(
            require_task_state([TaskState.new]),
        ),
    ],
)
async def upload_scan(
    request: Request,
    title_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    scan_data: UploadFile,
    db=Depends(get_db),
):
    """Uploads image (scan) to volume storage and links it to the title.

    Returns:
        dict: Title ID and filename of the uploaded scan.
    """
    # Create scan object
    scan = save_scan_to_storage(title_id, scan_data.file, scan_data.filename)

    # Update title entry in DB
    await db.titles.update_one(
        {"_id": ObjectId(title_id)},
        {
            "$push": {
                "filelist": scan.filename,
                "scans": scan.model_dump(by_alias=True),
            },
            "$set": {"modified_at": datetime.now(), "modified_by": current_user.email},
        },
    )

    logger.info(f"Uploaded scan '{scan_data.filename}' to title '{title_id}'")
    return {"id": title_id, "filename": scan_data.filename}


@limiter.limit("60/minute;600/hour")
@router.post(
    "/{title_id}/process",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=from_title_id)
        ),
        Depends(
            require_task_state([TaskState.new]),
        ),
    ],
)
async def process_title(request: Request, title_id: str, db=Depends(get_db)):
    """Moves title into state 'scheduled' and starts the ML prediction workflow.

    Returns:
        dict: Title ID and new state.
    """
    logger.info(f"Scheduling workflow for title ID: {title_id}")
    title = await db.titles.find_one({"_id": ObjectId(title_id)})

    # Schedule task and update state
    try:
        await autocrop_workflow.aio_run_no_wait(input=Title.model_validate(title))
        await db.titles.update_one(
            {"_id": ObjectId(title_id)},
            {"$set": {"state": TaskState.scheduled, "modified_at": datetime.now()}},
        )
    except Exception as e:
        logger.error(f"Failed to schedule workflow for title ID {title_id}: {e}")
        raise HTTPException(500, f"Failed to schedule workflow: {e}")

    logger.info(f"{title_id} moved to state 'scheduled'")
    return {"state": TaskState.scheduled, "id": str(title["_id"])}


@router.get(
    "/{title_id}/status",
    dependencies=[
        Depends(
            require_group_permission(
                Permission.read_title, group_id_provider=from_title_id
            )
        )
    ],
)
async def get_title_state(title_id: str, db=Depends(get_db)):
    """Gets the current state of a title by its ID.

    Returns:
        str: Current state of the title.
    """
    title = await db.titles.find_one({"_id": ObjectId(title_id)})
    return title.get("state")


@router.get(
    "/{title_id}/scans",
    dependencies=[
        Depends(
            require_group_permission(
                Permission.read_title, group_id_provider=from_title_id
            )
        )
    ],
)
async def get_scans(title_id: str, scan_id: str | None = None, db=Depends(get_db)):
    """Gets crop instructions for all pages, can be filtered to get specific scan page by ID.

    Returns:
        list: List of scans with page crop instructions.
    """
    if scan_id:
        title = await db.titles.find_one(
            {"scans._id": ObjectId(scan_id), "_id": ObjectId(title_id)},
            {"scans": {"$elemMatch": {"_id": ObjectId(scan_id)}}},
        )
    else:
        title = await db.titles.find_one({"_id": ObjectId(title_id)})

    scans = [Scan(**scan) for scan in title.get("scans", [])]
    scans = sorted(scans, key=lambda s: s.filename)
    title["scans"] = format_page_data_list(scans)
    title = jsonable_encoder(
        title, custom_encoder={ObjectId: str}, exclude=["filelist"]
    )

    logger.info(f"Fetched {len(title.get('scans', []))} scans for title ID: {title_id}")

    return title


@limiter.limit("60/minute;600/hour")
@router.get(
    "/{title_id}/predicted-scans",
    dependencies=[
        Depends(
            require_group_permission(
                Permission.read_title, group_id_provider=from_title_id
            )
        )
    ],
)
async def get_predicted_pages(request: Request, title_id: str, db=Depends(get_db)):
    """Gets predictions for all scans (without user edits).

    Returns:
        list: List of scans with predicted page crop instructions.
    """
    title = await db.titles.find_one({"_id": ObjectId(title_id)})

    scans = [Scan(**scan) for scan in title.get("scans", [])]
    scans = sorted(scans, key=lambda s: s.filename)
    title["scans"] = format_predicted(scans)
    title = jsonable_encoder(
        title, custom_encoder={ObjectId: str}, exclude=["filelist"]
    )

    logger.info(
        f"Fetched {len(title.get('scans', []))} predicted scans for title ID: {title_id}"
    )
    return title


@limiter.limit("2000/minute")
@router.get(
    "/{title_id}/files",
    response_class=Response,
    dependencies=[
        Depends(
            require_group_permission(
                Permission.read_title, group_id_provider=from_title_id
            )
        )
    ],
)
async def get_scanfile(
    request: Request, title_id: str, scan_id: str, db=Depends(get_db)
):
    """Gets image of a specific scan.

    Returns:
        Response: Image file response.
    """
    scan = await db.titles.find_one(
        {"scans._id": ObjectId(scan_id), "_id": ObjectId(title_id)},
        {"scans": {"$elemMatch": {"_id": ObjectId(scan_id)}}},
    )
    scan = scan["scans"][0]
    if not scan:
        raise HTTPException(404, "Scan not found")

    # Get file and filetype
    try:
        file = open(scan["filename"], "rb").read()
        media_type = sniff_media_type(file[:16])
    except Exception as e:
        raise HTTPException(500, f"Failed to read scan file: {e}")

    # Convert TIFF to JPEG on the fly
    if media_type == "image/tiff":
        media_type = "image/jpeg"
        file = resize_image(scan["filename"], (1024, 1024))
    return Response(content=file, media_type=media_type)


@limiter.limit("2000/minute")
@router.get(
    "/{title_id}/thumbnails",
    response_class=Response,
    dependencies=[
        Depends(
            require_group_permission(
                Permission.read_title, group_id_provider=from_title_id
            )
        )
    ],
)
async def get_thumbnail(
    request: Request, title_id: str, scan_id: str, db=Depends(get_db)
):
    """Gets a thumbnail for a specific scan of a title.

    Returns:
        list: List of thumbnail image responses.
    """
    scan = await db.titles.find_one(
        {"scans._id": ObjectId(scan_id), "_id": ObjectId(title_id)},
        {"scans": {"$elemMatch": {"_id": ObjectId(scan_id)}}},
    )
    scan = scan["scans"][0]
    if not scan:
        raise HTTPException(404, "Scan not found")

    thumbnail = resize_image(scan["filename"])
    return Response(content=thumbnail, media_type="image/jpeg")


@limiter.limit("60/minute;600/hour")
@router.patch(
    "/{title_id}/update-pages",
    dependencies=[
        Depends(
            require_group_permission(Permission.write, group_id_provider=from_title_id)
        ),
        Depends(require_task_state([TaskState.ready, TaskState.user_approved])),
    ],
)
async def update_pages(
    request: Request,
    title_id: str,
    scans: list[ScanUpdate],
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
):
    """Updates predicted coordinates with user input for a given title ID.

    Returns:
        dict: Title ID.
    """
    logger.info(f"Updating pages for title ID: {title_id}")
    for scan in scans:
        pages = [page.model_dump(by_alias=True) for page in scan.pages]

        result = await db.titles.update_one(
            {"_id": ObjectId(title_id), "scans._id": scan.id},
            {
                "$set": {
                    "scans.$.user_edited_pages": pages,
                }
            },
        )
        if result.matched_count == 0:
            raise HTTPException(404, f"Scan with id {scan.id} not found")

    # Update title state to user_approved
    await db.titles.update_one(
        {"_id": ObjectId(title_id)},
        {
            "$set": {
                "state": TaskState.user_approved,
                "modified_at": datetime.now(),
                "modified_by": current_user.email,
            }
        },
    )
    logger.info(
        f"{current_user.email} patched {len(scans)} scans and updated state to user_approved for title ID: {title_id}"
    )
    return {"id": title_id}


@limiter.limit("60/minute;600/hour")
@router.delete(
    "/{title_id}",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=from_title_id)
        )
    ],
)
async def delete_title(request: Request, title_id: str, db=Depends(get_db)):
    """Deletes a title and all associated saved files."""
    title = await db.titles.find_one({"_id": ObjectId(title_id)})
    title = Title.model_validate(title)

    try:
        await delete_title_from_db_and_storage(title, db)
    except Exception as e:
        logger.error(f"Failed to delete title ID {title_id}: {e}")
        raise HTTPException(500, f"Failed to delete title: {e}")

    return {"detail": "Title and associated scans deleted"}


@limiter.limit("60/minute;600/hour")
@router.patch(
    "/{title_id}/reset",
    dependencies=[
        Depends(
            require_group_permission(Permission.write, group_id_provider=from_title_id)
        )
    ],
)
async def reset_predictions(
    request: Request,
    title_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
):
    """Resets all predictions for a given title ID. User edits are permanently removed from database.

    Returns:
        list: List of scans with predicted page crop instructions."""
    logger.info(f"Resetting predictions for title ID: {title_id}")
    result = await db.titles.update_one(
        {"_id": ObjectId(title_id)},
        {
            "$set": {
                "scans.$[].user_edited_pages": None,
                "modified_at": datetime.now(),
                "modified_by": current_user.email,
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(404, f"Title with id {title_id} not found")

    return await get_scans(title_id, None, db)


@limiter.limit("60/minute;600/hour")
@router.patch(
    "/{title_id}",
    dependencies=[
        Depends(
            require_group_permission(Permission.upload, group_id_provider=from_title_id)
        ),
        Depends(
            require_task_state(
                [
                    TaskState.ready,
                    TaskState.user_approved,
                    TaskState.new,
                    TaskState.failed,
                ]
            )
        ),
    ],
)
async def update_title(
    request: Request,
    title_id: str,
    title_data: TitleUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
):
    """Updates title metadata for a given title ID.

    Returns:
        dict: Updated title data.
    """
    logger.info(f"Updating title metadata for title ID: {title_id}")
    update_data = title_data.model_dump(exclude_unset=True, by_alias=True)
    update_data["modified_at"] = datetime.now()
    update_data["modified_by"] = current_user.email
    if update_data.get("settings"):
        update_data["state"] = TaskState.new
        update_data["scans"] = []  # Clear scans if settings are updated

    result = await db.titles.update_one(
        {"_id": ObjectId(title_id)},
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(404, f"Title with id {title_id} not found")

    updated_title = await db.titles.find_one({"_id": ObjectId(title_id)})

    # If settings are updated, reset state to new and schedule workflow
    if update_data.get("settings"):
        logger.info(f"Scheduling workflow for title ID: {title_id}")
        try:
            await autocrop_workflow.aio_run_no_wait(
                input=Title.model_validate(updated_title)
            )
            await db.titles.update_one(
                {"_id": ObjectId(title_id)},
                {"$set": {"state": TaskState.scheduled, "modified_at": datetime.now()}},
            )
        except Exception as e:
            logger.error(f"Failed to schedule workflow for title ID {title_id}: {e}")
            raise HTTPException(500, f"Failed to schedule workflow: {e}")

    return jsonable_encoder(
        updated_title, custom_encoder={ObjectId: str}, exclude=["filelist", "scans"]
    )
