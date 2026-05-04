from datetime import datetime
import logging
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.encoders import jsonable_encoder
from fastapi.security import OAuth2PasswordRequestForm
from app.api.setup_db import get_db
from app.db.operations.api import add_group_name_to_user_response
from app.api.authz import from_title_id, require_role
from app.db.schemas.user import Role, User, UserCreate, UserUpdate
from app.api.authn import (
    Token,
    authenticate_user,
    create_access_token,
    get_current_user,
    get_password_hash,
)
from app.api.limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])


@limiter.limit("10/minute;20/hour")
@router.post("/login")
async def login_for_access_token(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db=Depends(get_db),
) -> Token:
    """Login to obtain an access token."""
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={
            "type": "user",
            "sub": user["email"],
            "role": user["role"],
        },
    )

    return Token(access_token=access_token, token_type="bearer")


@limiter.limit("120/minute")
@router.get("/current-user", dependencies=[Depends(require_role(Role.user))])
async def me(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
    title_id: str | None = None,
):
    """
    Get current user info. If title_id is provided, filter permissions to that title's group only.
    """
    logger.info(f"Fetching current user info for user ID: {current_user.id}")
    if title_id:
        title_group = await from_title_id(title_id, db)
        permissions = [
            perm
            for perm in current_user.permissions
            if str(perm.group_id) == title_group
        ]
        current_user.permissions = permissions
    return jsonable_encoder(current_user, exclude=["password"])


@limiter.limit("120/minute")
@router.get(
    "",
    dependencies=[Depends(require_role(Role.admin))],
)
async def get_all_users(
    request: Request, group_id: str | None = None, db=Depends(get_db)
):
    """List all users, can be filtered by group ID. Admin only."""
    if group_id:
        users = await db.users.find(
            {"permissions.group_id": ObjectId(group_id)}
        ).to_list()
    else:
        users = await db.users.find({}).to_list(length=None)

    # Collect unique group_ids from all users
    group_ids = {
        p["group_id"]
        for u in users
        for p in (u.get("permissions") or [])
        if p.get("group_id") is not None
    }

    groups = await db.groups.find(
        {"_id": {"$in": list(group_ids)}},
        {"name": 1},
    ).to_list(length=None)

    group_name_by_id = {g["_id"]: g["name"] for g in groups}

    # Enrich users in-memory
    for u in users:
        for p in u.get("permissions") or []:
            gid = p.get("group_id")
            p["group_name"] = group_name_by_id.get(gid)

    logger.info(f"Fetched {len(users)} users with permissions for groups: {group_ids}")
    # users now contains permissions[*].group_name
    return jsonable_encoder(users, exclude=["password"], custom_encoder={ObjectId: str})


@limiter.limit("120/minute")
@router.get(
    "/{user_id}",
    dependencies=[Depends(require_role(Role.admin))],
)
async def get_user(request: Request, user_id: str, db=Depends(get_db)):
    """Get a user by ID. Admin only."""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user = await add_group_name_to_user_response(User.model_validate(user), db)
    logger.info(f"Fetched user details for user ID: {user['_id']}")
    return jsonable_encoder(user, exclude=["password"], custom_encoder={ObjectId: str})


@limiter.limit("10/minute;20/hour")
@router.post(
    "/register",
    dependencies=[Depends(require_role(Role.admin))],
)
async def register_user(request: Request, user: UserCreate, db=Depends(get_db)):
    """Register new user. Admin only."""
    try:
        created_user = User.model_validate(user.model_dump(by_alias=True))
        doc = created_user.model_dump(by_alias=True)
        unhashed_password = doc["password"]
        doc["password"] = get_password_hash(unhashed_password)

        inserted_user = await db.users.insert_one(doc)
    except Exception as e:
        logger.error(f"Failed to register user: {e}")
        raise HTTPException(
            status_code=400,
            detail=e.__class__.__name__,
        )
    logger.info(f"Registered new user with ID: {inserted_user.inserted_id}")
    return {
        "id": str(inserted_user.inserted_id),
        "password": unhashed_password,
        "detail": "User created successfully",
    }


@limiter.limit("60/minute;600/hour")
@router.patch(
    "/{user_id}",
    dependencies=[Depends(require_role(Role.admin))],
)
async def update_user(
    request: Request,
    user_id: str,
    user: UserUpdate,
    db=Depends(get_db),
):
    """Update an existing user. Admin only."""
    existing_user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not existing_user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    try:
        update_data = {
            k: v for k, v in user.model_dump(by_alias=True).items() if v is not None
        }
        update_data["modified_at"] = datetime.now()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update_data})
        updated_user = await db.users.find_one({"_id": ObjectId(user_id)})
        updated_user = await add_group_name_to_user_response(
            User.model_validate(updated_user), db
        )
    except Exception as e:
        logger.error(f"Failed to update user ID {user_id}: {e}")
        raise HTTPException(
            status_code=400,
            detail=e.__class__.__name__,
        )
    logger.info(f"Updated user ID {user_id}, new data: {update_data}")
    return jsonable_encoder(updated_user, custom_encoder={ObjectId: str})


@limiter.limit("10/minute;20/hour")
@router.patch(
    "/{user_id}/reset-password",
    dependencies=[Depends(require_role(Role.admin))],
)
async def reset_password(
    request: Request,
    user_id: str,
    db=Depends(get_db),
):
    """Reset user password. Admin only."""
    existing_user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not existing_user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    new_password = User.create_random_password()
    hashed_password = get_password_hash(new_password)

    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password": hashed_password, "modified_at": datetime.now()}},
    )

    logger.info(f"Reset password for user ID {user_id}")
    return {"detail": "Password reset successfully", "new_password": new_password}


@limiter.limit("10/minute;20/hour")
@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.admin))],
)
async def delete_user(
    request: Request,
    user_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
):
    """Delete user. Admin only."""
    existing_user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not existing_user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )
    if str(existing_user["_id"]) == str(current_user.id):
        raise HTTPException(
            status_code=400,
            detail="Cannot delete self",
        )
    await db.users.delete_one({"_id": ObjectId(user_id)})
    logger.info(f"Deleted user ID {user_id}")
    return {"detail": "User deleted"}
