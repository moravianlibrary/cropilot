from datetime import datetime
import logging
import os
from altair import Title
from bson import ObjectId
from fastapi.encoders import jsonable_encoder

from app.api.utils import remove_title_from_storage
from app.db.schemas.title import Settings
from app.db.schemas.user import Role, User

UPLOAD_VOLUME_PATH = os.getenv("SCANS_VOLUME_PATH")
logger = logging.getLogger(__name__)


async def link_titles_to_group_bulk(title_ids: list[ObjectId], group_id: ObjectId, db):
    """Link multiple titles to a group."""
    await db.titles.update_many(
        {"_id": {"$in": title_ids}},
        {"$set": {"group_id": group_id}},
    )
    await db.groups.update_one(
        {"_id": group_id},
        {
            "$addToSet": {"title_ids": {"$each": title_ids}},
            "$set": {"modified_at": datetime.now()},
        },
    )

    logger.debug(f"Linked titles {title_ids} to group {group_id}")
    return {"title_ids": title_ids, "group_id": group_id}


async def get_users_in_group(group_id: ObjectId, db):
    """Get all users in a specific group."""
    users = await db.users.find(
        {"permissions.group_id": group_id, "role": Role.user},
    ).to_list(length=None)

    for user in users:
        user["permission"] = await get_user_permissions_in_group(User.model_validate(user), group_id)

    return jsonable_encoder(
        users,
        custom_encoder={ObjectId: str},
        include=["_id", "full_name", "permission"],
    )


async def get_user_permissions_in_group(current_user: User, group_id: ObjectId):
    """Get the permissions of the current user in a specific group."""
    for perm in current_user.permissions:
        if perm.group_id == group_id:
            return perm.permission
    logger.debug(f"User {current_user.email} has no permissions in group ID {group_id}")
    return None


async def add_group_name_to_user_response(user: User, db) -> dict:
    """Add group names to user's permissions in the response."""
    user_dict = user.model_dump(by_alias=True)
    for perm in user_dict.get("permissions", []):
        group = await db.groups.find_one({"_id": perm["group_id"]})
        perm["group_name"] = group["name"] if group else None
        logger.debug(
            f"Added group name '{perm['group_name']}' to user '{user.email}' permissions for group ID {perm['group_id']}"
        )
    return user_dict


async def set_default_title_params(title: Title, group_id: str, db) -> Title:
    """Sets default title parameters based on group settings."""
    if title.external_id is None:
        title.external_id = str(title.id)
    # Override title settings with group default if not provided
    if title.settings is None:
        settings = (
            await db.groups.find_one(
                {"_id": ObjectId(group_id)}, {"default_settings": 1}
            )
        )["default_settings"]
        title.settings = Settings.model_validate(settings)
        logger.debug(
            f"Set default crop model '{title.settings.crop_model}' for title based on group settings"
        )

    return title


async def delete_title_from_db_and_storage(title_id: str, group_id: str, db):
    """Deletes a title and its associated scans from the database."""

    # Delete from group
    await db.groups.update_one(
        {"_id": ObjectId(group_id)},
        {"$pull": {"title_ids": ObjectId(title_id)}},
    )
    # Delete from storage
    remove_title_from_storage(title_id)
    # Delete from db
    deleted_title = await db.titles.delete_one({"_id": ObjectId(title_id)})
    logger.info(f"Deleted title from DB: {deleted_title}")
