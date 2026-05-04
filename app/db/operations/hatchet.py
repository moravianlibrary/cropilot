from datetime import datetime
import logging
from app.db.schemas.title import Scan, TaskState, Title
from bson import ObjectId

logger = logging.getLogger(__name__)


def db_update_task_state(title_id: str, new_state: TaskState, db):
    """Update the state of a Hatchet task, store into Title object."""
    if not ObjectId.is_valid(title_id):
        raise ValueError(f"Invalid title id: {title_id}")
    result = db.titles.update_one(
        {"_id": ObjectId(title_id)},
        {"$set": {"state": new_state, "modified_at": datetime.now()}},
    )
    if result.matched_count == 0:
        raise Exception(f"Title not found: {title_id}")

    logger.debug(f"Updated title {title_id} to state {new_state}")
    return {"status": "success", "updated_count": result.modified_count}


def db_get_state(title_id: str, db) -> TaskState | None:
    """Get the state of a title."""
    if not ObjectId.is_valid(title_id):
        raise ValueError(f"Invalid title id: {title_id}")
    title = db.titles.find_one({"_id": ObjectId(title_id)})
    if not title:
        raise Exception(f"Title not found: {title_id}")

    return title.get("state")


def db_create_title(title_data: Title, db):
    """Create a new title."""
    doc = title_data.model_dump(by_alias=True)
    result = db.titles.insert_one(doc)

    logger.debug(f"Created new title with ID: {result.inserted_id}")
    return {"title_id": str(result.inserted_id)}


def db_replace_scans(title_id: ObjectId, pages_data: list[Scan], db):
    """Replace all pages of a title."""
    docs = [page.model_dump(by_alias=True) for page in pages_data]

    db.titles.update_one(
        {"_id": ObjectId(title_id)},
        {"$set": {"scans": docs}},
    )

    logger.debug(f"Replaced scans for title {title_id}")
    return {"title_id": title_id, "replaced_count": len(docs)}
