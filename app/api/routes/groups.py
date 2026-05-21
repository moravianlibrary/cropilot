from datetime import datetime
import logging
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from app.api.limiter import limiter
from app.api.routes.models import list_models
from app.api.setup_db import get_db
from app.api.authz import from_group_id, require_group_permission, require_role
from app.db.operations.api import (
    delete_title_from_db_and_storage,
    get_user_permissions_in_group,
    get_users_in_group,
)
from app.db.schemas.group import APIkey, Group, GroupCreate, GroupUpdate
from app.db.schemas.title import Title
from app.db.schemas.user import Maintains, Permission, PermissionRequest, Role, User
from app.api.authn import (
    get_current_user,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/groups", tags=["Groups"])


@limiter.limit("60/minute;600/hour")
@router.get("")
async def list_groups(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
):
    """Lists all groups user belongs to."""
    group_read_permission_ids = [
        ObjectId(perm.group_id)
        for perm in current_user.permissions
        if Permission.read_group in perm.permission
    ]
    groups = await db.groups.find({"_id": {"$in": group_read_permission_ids}}).to_list(
        length=None
    )
    for group in groups:
        # Display permissions inside every group
        group["permissions"] = await get_user_permissions_in_group(
            current_user, ObjectId(group["_id"])
        )
        # Admin user can also see list of users and api_key
        if current_user.role == Role.admin:
            group["users"] = await get_users_in_group(ObjectId(group["_id"]), db)
        else:
            group.pop("api_key", None)

        # Replace title_ids with title_count
        group["title_count"] = len(group["title_ids"])
        group.pop("title_ids", None)

    logger.info(
        f"Listed groups for user ID {current_user.id}: {[str(group['_id']) for group in groups]}"
    )
    return jsonable_encoder(groups, custom_encoder={ObjectId: str})


@limiter.limit("2000/minute")
@router.get(
    "/{group_id}",
    dependencies=[
        Depends(
            require_group_permission(
                Permission.read_group, group_id_provider=from_group_id
            )
        )
    ],
)
async def get_titles(request: Request, group_id: str, db=Depends(get_db)):
    """Gets all titles from the database.

    Returns:
        dict: Titles containing their IDs, states, creation and modification dates.
    """
    if not ObjectId.is_valid(group_id):
        raise HTTPException(400, f"ID '{group_id}' is not a valid ObjectId")

    group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    titles = await db.titles.find(
        {"group_id": ObjectId(group_id)},
        {
            "_id": 1,
            "state": 1,
            "created_at": 1,
            "modified_at": 1,
            "external_id": 1,
            "settings": 1,
        },
    ).to_list(None)

    # Show most recently created titles first
    titles = sorted(titles, key=lambda x: x["created_at"], reverse=True)

    group["titles"] = titles
    logger.info(f"Fetched {len(titles)} titles for group ID {group_id}")
    return jsonable_encoder(
        group, custom_encoder={ObjectId: str}, exclude=["title_ids", "api_key"]
    )


@limiter.limit("60/minute;600/hour")
@router.post("", dependencies=[Depends(require_role(Role.admin))])
async def create_group(
    request: Request,
    group: GroupCreate,
    db=Depends(get_db),
):
    """Creates a new group."""
    models = await list_models()
    if group.default_settings.crop_model not in models["crop_models"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{group.default_settings.crop_model}' does not exist",
        )
    if group.default_settings.rotation_model not in models["rotation_models"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{group.default_settings.rotation_model}' does not exist",
        )

    try:
        group = Group.model_validate(group.model_dump()).model_dump(by_alias=True)
        result = await db.groups.insert_one(group)
    except Exception as e:
        if "duplicate key error" in str(e).lower():
            logger.warning(
                f"Attempt to create duplicate group with name '{group['name']}'"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Group with name '{group['name']}' already exists",
            )
        logger.error(f"Failed to create group: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create group",
        )

    # Add admins to group
    admin_users = await db.users.find({"role": Role.admin.value}).to_list(length=None)
    new_permission = Maintains(
        group_id=str(result.inserted_id), permission=list(Permission)
    ).model_dump()
    await db.users.update_many(
        {"_id": {"$in": [user["_id"] for user in admin_users]}},
        {"$push": {"permissions": new_permission}},
    )

    logger.info(
        f"Created group '{group['name']}' with ID {result.inserted_id} and added {len(admin_users)} admin users to it"
    )
    return {"id": str(result.inserted_id), "api_key": group["api_key"]["key"]}


@limiter.limit("60/minute;600/hour")
@router.post("/{group_id}/members", dependencies=[Depends(require_role(Role.admin))])
async def bulk_add_group_members(
    request: Request,
    group_id: str,
    permission_requests: list[PermissionRequest],
    db=Depends(get_db),
):
    """Updates group members and their permissions."""
    group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Check if we can perform update - if user is already a member of the group, we cannot add them again
    for perm_request in permission_requests:
        user_permission = await db.users.find_one(
            {
                "_id": ObjectId(perm_request.user_id),
                "permissions.group_id": ObjectId(group_id),
            },
            {"permissions.$": 1},
        )
        if user_permission:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User {perm_request.user_id} is already a member of the group",
            )
        if not perm_request.user_permissions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Permissions cannot be empty",
            )
    # Post request
    for perm_request in permission_requests:
        new_permission = Maintains(
            group_id=group_id, permission=perm_request.user_permissions
        ).model_dump()
        await db.users.update_one(
            {"_id": ObjectId(perm_request.user_id)},
            {"$push": {"permissions": new_permission}},
        )

    logger.info(f"Added {len(permission_requests)} members to group ID {group_id}")
    return {"detail": "Group members added"}


@limiter.limit("60/minute;600/hour")
@router.patch("/{group_id}/members", dependencies=[Depends(require_role(Role.admin))])
async def bulk_update_group_members(
    request: Request,
    group_id: str,
    permission_requests: list[PermissionRequest],
    db=Depends(get_db),
):
    """Updates group members and their permissions."""
    group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Check if we can perform update - if user is not a member of the group, we cannot update permissions
    admin_user_ids = await db.users.find({"role": Role.admin.value}).to_list(
        length=None
    )
    admin_user_ids = [user["_id"] for user in admin_user_ids]
    for perm_request in permission_requests:
        user_permission = await db.users.find_one(
            {
                "_id": ObjectId(perm_request.user_id),
                "permissions.group_id": ObjectId(group_id),
            },
            {"permissions.$": 1},
        )
        if not user_permission:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User {perm_request.user_id} is not a member of the group",
            )
        if ObjectId(perm_request.user_id) in admin_user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot change permissions for admin user {perm_request.user_id}",
            )
        if not perm_request.user_permissions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Permissions cannot be empty",
            )
    # Patch request
    for perm_request in permission_requests:
        await db.users.update_one(
            {
                "_id": ObjectId(perm_request.user_id),
                "permissions.group_id": ObjectId(group_id),
            },
            {
                "$set": {
                    "permissions.$.permission": perm_request.user_permissions,
                    "modified_at": datetime.now(),
                }
            },
        )

    logger.info(
        f"Updated permissions for {len(permission_requests)} members in group ID {group_id}"
    )
    return {"detail": "Group members updated"}


@limiter.limit("60/minute;600/hour")
@router.delete("/{group_id}/members", dependencies=[Depends(require_role(Role.admin))])
async def bulk_remove_group_members(
    request: Request,
    group_id: str,
    user_ids: list[str],
    db=Depends(get_db),
):
    group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Check if we can perform update - prevent removing admin users
    admin_user_ids = await db.users.find({"role": Role.admin.value}).to_list(
        length=None
    )
    admin_user_ids = [user["_id"] for user in admin_user_ids]
    for user_id in user_ids:
        if ObjectId(user_id) in admin_user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove admin users from group",
            )
    # Delete request
    await db.users.update_many(
        {"_id": {"$in": [ObjectId(user_id) for user_id in user_ids]}},
        {
            "$pull": {"permissions": {"group_id": ObjectId(group_id)}},
            "$set": {"modified_at": datetime.now()},
        },
    )

    logger.info(f"Removed {len(user_ids)} members from group ID {group_id}")
    return {"detail": "Group members removed"}


@limiter.limit("60/minute;600/hour")
@router.patch("/{group_id}", dependencies=[Depends(require_role(Role.admin))])
async def update_group(
    request: Request,
    group_id: str,
    group: GroupUpdate,
    db=Depends(get_db),
):
    """Updates group details."""
    existing_group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not existing_group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    update_data = {k: v for k, v in group.model_dump().items() if v is not None}
    if update_data.get("default_settings"):
        models = await list_models()
        if (
            update_data["default_settings"]["crop_model"]
            not in models["crop_models"]
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{update_data['default_settings']['crop_model']}' does not exist",
            )
        if (
            update_data["default_settings"]["rotation_model"]
            not in models["rotation_models"]
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{update_data['default_settings']['rotation_model']}' does not exist",
            )

    update_data["modified_at"] = datetime.now()
    if update_data:
        await db.groups.update_one(
            {"_id": ObjectId(group_id)},
            {"$set": update_data},
        )

    logger.info(f"Updated group ID {group_id} with data: {update_data}")
    return {"detail": "Group updated"}


@limiter.limit("60/minute;600/hour")
@router.delete("/{group_id}", dependencies=[Depends(require_role(Role.admin))])
async def delete_group(request: Request, group_id: str, db=Depends(get_db)):
    """Deletes a group and its titles (!!)"""
    group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Cascade - Remove users from group
    deleted_users = await db.users.update_many(
        {"permissions.group_id": ObjectId(group_id)},
        {
            "$pull": {"permissions": {"group_id": ObjectId(group_id)}},
            "$set": {"modified_at": datetime.now()},
        },
    )
    logger.info(
        f"Removed group {group_id} from users: {deleted_users.modified_count} users updated"
    )
    # Cascade - Remove titles in the group
    titles = await db.titles.find({"group_id": ObjectId(group_id)}).to_list(length=None)
    for title in titles:
        title = Title.model_validate(title)
        await delete_title_from_db_and_storage(str(title.id), group_id, db)

    # Remove group
    await db.groups.delete_one({"_id": ObjectId(group_id)})
    logger.info(f"Deleted group ID {group_id} and its {len(titles)} titles")
    return {"detail": "Group deleted"}


@limiter.limit("1/minute")
@router.post(
    "/{group_id}/api-key",
    dependencies=[Depends(require_role(Role.admin))],
)
async def revoke_group_api_key(
    request: Request,
    group_id: str,
    db=Depends(get_db),
):
    """Revoke API key for the group and create a new one."""
    group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    new_api_key = APIkey()
    await db.groups.update_one(
        {"_id": ObjectId(group_id)},
        {"$set": {"api_key": new_api_key.model_dump(by_alias=True)}},
    )

    logger.info(f"Revoked API key for group ID {group_id} and generated a new one")
    return jsonable_encoder(new_api_key, custom_encoder={ObjectId: str})
