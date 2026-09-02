from datetime import datetime
import logging
import math
import re
from typing import Annotated

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from app.api.limiter import limiter
from app.api.routes.models import list_models
from app.api.setup_db import get_db
from app.api.authz import (
    from_group_id,
    require_group_any_permission,
    require_group_permission,
    require_group_permission_or_admin,
    require_role,
)
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

# Fields the groups list can be sorted by (maps API name -> stored field).
_GROUP_SORT_FIELDS = {
    "name": "name",
    "created_at": "created_at",
    "modified_at": "modified_at",
}


async def _enrich_group(group: dict, current_user: User, db) -> dict:
    """Adds per-group permissions / users / title_count to a raw group document."""
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
    return group


@limiter.limit("60/minute;600/hour")
@router.get("")
async def list_groups(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int | None, Query(ge=1)] = None,
    page_size: Annotated[int | None, Query(ge=1, le=500)] = None,
    search: Annotated[str | None, Query()] = None,
    sort_field: Annotated[str | None, Query()] = None,
    sort_direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    db=Depends(get_db),
):
    """Lists all groups the user belongs to.

    When any of ``page``/``page_size``/``search`` is provided, returns a
    paginated envelope ``{items, total, page, page_size, total_pages}`` with
    server-side search (name/description, and id for a valid ObjectId) and
    sorting. Otherwise returns the full list unchanged (backward compatible).
    """
    # A group is visible if the user can read the whole group (read_group) or can
    # at least reach individual titles in it (read_title, e.g. an intern who only
    # sees titles assigned to them). The title list itself is filtered per-user.
    group_visible_ids = [
        ObjectId(perm.group_id)
        for perm in current_user.permissions
        if Permission.read_group in perm.permission
        or Permission.read_title in perm.permission
    ]

    query: dict = {"_id": {"$in": group_visible_ids}}
    if search:
        search = search.strip()
        if search:
            escaped = re.escape(search)
            or_conditions: list[dict] = [
                {"name": {"$regex": escaped, "$options": "i"}},
                {"description": {"$regex": escaped, "$options": "i"}},
            ]
            if ObjectId.is_valid(search):
                or_conditions.append({"_id": ObjectId(search)})
            query["$or"] = or_conditions

    paginate = page is not None or page_size is not None or search is not None

    # Legacy behaviour: return the full (optionally searched) list.
    if not paginate:
        groups = await db.groups.find(query).to_list(length=None)
        for group in groups:
            await _enrich_group(group, current_user, db)
        logger.info(
            f"Listed groups for user ID {current_user.id}: {[str(group['_id']) for group in groups]}"
        )
        return jsonable_encoder(groups, custom_encoder={ObjectId: str})

    page = page or 1
    page_size = page_size or 50
    sort_key = _GROUP_SORT_FIELDS.get(sort_field or "", "created_at")
    sort_order = ASCENDING if sort_direction == "asc" else DESCENDING

    total = await db.groups.count_documents(query)
    total_pages = math.ceil(total / page_size) if total else 0

    groups = (
        await db.groups.find(query)
        .sort([(sort_key, sort_order), ("_id", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(length=page_size)
    )
    for group in groups:
        await _enrich_group(group, current_user, db)

    logger.info(
        f"Listed {len(groups)}/{total} groups for user ID {current_user.id} "
        f"(page {page}/{total_pages or 1}, size {page_size})"
    )
    return {
        "items": jsonable_encoder(groups, custom_encoder={ObjectId: str}),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# Fields the titles list can be sorted by (maps API name -> stored field).
_TITLE_SORT_FIELDS = {
    "external_id": "external_id",
    "created_at": "created_at",
    "modified_at": "modified_at",
    "state": "state",
}


@limiter.limit("2000/minute")
@router.get(
    "/{group_id}",
    dependencies=[
        Depends(
            require_group_any_permission(
                [Permission.read_group, Permission.read_title],
                group_id_provider=from_group_id,
            )
        )
    ],
)
async def get_titles(
    request: Request,
    group_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    search: Annotated[str | None, Query()] = None,
    sort_field: Annotated[str | None, Query()] = None,
    sort_direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    state: Annotated[str | None, Query()] = None,
    crop_model: Annotated[str | None, Query()] = None,
    rotation_model: Annotated[str | None, Query()] = None,
    db=Depends(get_db),
):
    """Gets a paginated, optionally filtered and sorted list of titles in a group.

    Query params:
        page: 1-based page number.
        page_size: Number of titles per page.
        search: Case-insensitive text matched against external_id / crop model
            (and the title ID when it is a valid ObjectId).
        sort_field: One of external_id, created_at, modified_at, state,
            assigned_to_name.
        sort_direction: asc or desc.
        state, crop_model, rotation_model: Optional exact-match filters.

    Returns:
        dict: The group with a paginated ``titles`` list, pagination metadata
        (total, page, page_size, total_pages) and ``filter_options`` holding the
        distinct crop/rotation models across the whole group.
    """
    if not ObjectId.is_valid(group_id):
        raise HTTPException(400, f"ID '{group_id}' is not a valid ObjectId")

    group = await db.groups.find_one({"_id": ObjectId(group_id)})
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Build the query: always scoped to the group, plus optional filters.
    query: dict = {"group_id": ObjectId(group_id)}

    # Users who can't read the whole group (read_title only, e.g. an intern) see
    # only the titles assigned to them. Admins and read_group holders see all.
    group_perms = await get_user_permissions_in_group(current_user, ObjectId(group_id))
    sees_whole_group = current_user.role == Role.admin or (
        group_perms is not None and Permission.read_group in group_perms
    )
    if not sees_whole_group:
        query["assigned_to"] = current_user.id

    if state:
        query["state"] = state
    if crop_model:
        query["settings.crop_model"] = crop_model
    if rotation_model:
        query["settings.rotation_model"] = rotation_model
    if search:
        search = search.strip()
        if search:
            escaped = re.escape(search)
            or_conditions: list[dict] = [
                {"external_id": {"$regex": escaped, "$options": "i"}},
                {"settings.crop_model": {"$regex": escaped, "$options": "i"}},
            ]
            if ObjectId.is_valid(search):
                or_conditions.append({"_id": ObjectId(search)})
            query["$or"] = or_conditions

    # Resolve sorting (defaults to newest first). `assigned_to_name` is special:
    # the name isn't stored on the title (it lives in `users`), so it can't be
    # ordered by a plain `.find().sort()` — it needs a $lookup (handled below).
    sort_by_assignee = sort_field == "assigned_to_name"
    sort_key = _TITLE_SORT_FIELDS.get(sort_field or "", "created_at")
    sort_order = ASCENDING if sort_direction == "asc" else DESCENDING

    total = await db.titles.count_documents(query)
    total_pages = math.ceil(total / page_size) if total else 0

    projection = {
        "_id": 1,
        "state": 1,
        "created_at": 1,
        "modified_at": 1,
        "external_id": 1,
        "settings": 1,
        "assigned_to": 1,
    }

    if sort_by_assignee:
        # Sort by the assignee's display name. Because that name is resolved from
        # the `users` collection (not stored on the title), order globally — before
        # paging — with an aggregation that $lookups the name, sorts, then pages.
        titles = await db.titles.aggregate(
            [
                {"$match": query},
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "assigned_to",
                        "foreignField": "_id",
                        "as": "_assignee",
                    }
                },
                {
                    "$addFields": {
                        "assigned_to_name": {
                            "$ifNull": [
                                {"$arrayElemAt": ["$_assignee.full_name", 0]},
                                None,
                            ]
                        }
                    }
                },
                {"$sort": {"assigned_to_name": sort_order, "_id": ASCENDING}},
                {"$skip": (page - 1) * page_size},
                {"$limit": page_size},
                {"$project": {**projection, "assigned_to_name": 1}},
            ]
        ).to_list(length=page_size)
    else:
        titles = (
            await db.titles.find(query, projection)
            .sort([(sort_key, sort_order), ("_id", ASCENDING)])
            .skip((page - 1) * page_size)
            .limit(page_size)
            .to_list(length=page_size)
        )

        # Resolve the assignee's id to a display name. Additive `assigned_to_name`
        # field; `assigned_to` (id) is kept so the UI knows the current assignee.
        # (The assignee-sorted path above already resolves this via the $lookup.)
        assignee_ids = {t["assigned_to"] for t in titles if t.get("assigned_to")}
        name_by_id: dict[ObjectId, str | None] = {}
        if assignee_ids:
            assignees = await db.users.find(
                {"_id": {"$in": list(assignee_ids)}},
                {"full_name": 1},
            ).to_list(length=len(assignee_ids))
            name_by_id = {u["_id"]: u.get("full_name") for u in assignees}
        for title in titles:
            title["assigned_to_name"] = name_by_id.get(title.get("assigned_to"))

    group["titles"] = titles
    group["total"] = total
    group["page"] = page
    group["page_size"] = page_size
    group["total_pages"] = total_pages

    # Distinct filter options span the whole group and don't change between pages,
    # so only compute them on the first page (each distinct is a full group scan).
    # The frontend keeps the previously received options for subsequent pages.
    if page == 1:
        group_scope = {"group_id": ObjectId(group_id)}
        crop_models = await db.titles.distinct("settings.crop_model", group_scope)
        rotation_models = await db.titles.distinct(
            "settings.rotation_model", group_scope
        )
        group["filter_options"] = {
            "crop_models": sorted(m for m in crop_models if m),
            "rotation_models": sorted(m for m in rotation_models if m),
        }
    else:
        group["filter_options"] = None

    logger.info(
        f"Fetched {len(titles)}/{total} titles for group ID {group_id} "
        f"(page {page}/{total_pages or 1}, size {page_size})"
    )
    return jsonable_encoder(
        group, custom_encoder={ObjectId: str}, exclude=["title_ids", "api_key"]
    )


@limiter.limit("60/minute;600/hour")
@router.get(
    "/{group_id}/assignable-users",
    dependencies=[
        Depends(
            require_group_permission_or_admin(
                Permission.upload, group_id_provider=from_group_id
            )
        )
    ],
)
async def list_assignable_users(
    request: Request,
    group_id: str,
    db=Depends(get_db),
):
    """Lists group members a title can be assigned to (anyone with ``read_title``).

    Requires ``upload`` (a manager) on the group. Returns ``{_id, full_name}``.
    """
    if not ObjectId.is_valid(group_id):
        raise HTTPException(400, f"ID '{group_id}' is not a valid ObjectId")

    # Only regular members are assignable — exclude admins and the public user.
    users = await db.users.find(
        {
            "role": Role.user.value,
            "email": {"$ne": "public@user.cropilot"},
            "permissions": {
                "$elemMatch": {
                    "group_id": ObjectId(group_id),
                    "permission": Permission.read_title.value,
                }
            },
        },
        {"_id": 1, "full_name": 1},
    ).to_list(length=None)

    return jsonable_encoder(users, custom_encoder={ObjectId: str})


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
        if not ObjectId.is_valid(perm_request.user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"ID '{perm_request.user_id}' is not a valid ObjectId",
            )
        # Only regular users can be added to a group — never admins.
        target_user = await db.users.find_one(
            {"_id": ObjectId(perm_request.user_id)}, {"role": 1}
        )
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User {perm_request.user_id} not found",
            )
        if target_user.get("role") == Role.admin.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admins cannot be added as group members",
            )

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
        if update_data["default_settings"]["crop_model"] not in models["crop_models"]:
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
