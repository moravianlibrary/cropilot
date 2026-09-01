from bson import ObjectId
from fastapi import Depends, HTTPException, status
from app.api.setup_db import get_db
from app.db.schemas.user import Role, Permission, User
from app.api.authn import get_current_user

import logging

logger = logging.getLogger(__name__)


class RoleGuard:
    """Guard to check if the user has the required (admin, user) role."""

    def __init__(self, required_role: Role):
        self.required_role = required_role
        self.role_mapping = {
            Role.user: 1,
            Role.admin: 2,
        }

    def __call__(self, user: User = Depends(get_current_user)):
        logger.info(
            f"Checking role: user role={user.role}, required role={self.required_role}"
        )
        if self.role_mapping[user.role] >= self.role_mapping[self.required_role]:
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient role privileges",
        )


class GroupGuard:
    """Guard to check if the user is a member of a required group."""

    def __init__(self, required_permission: Permission):
        self.required_permission = required_permission

    def __call__(self, group_id: str, user: User = Depends(get_current_user)):
        # If group_id is None, check if user has the required permission in any group
        if group_id is None:
            all_permissions_types = set()
            for perm in user.permissions:
                all_permissions_types.update(perm.permission)
            logger.info(
                f"Checking permissions in any group: user permissions={all_permissions_types}, required permission={self.required_permission}"
            )
            if self.required_permission in all_permissions_types:
                return user
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        # Else, check if user has the required permission in the specified group
        for perm in user.permissions:
            if str(perm.group_id) == group_id:
                if self.required_permission in perm.permission:
                    return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient group permissions",
        )


class GroupAnyGuard:
    """Guard that passes if the user holds ANY of the required permissions in the group."""

    def __init__(self, required_permissions: list[Permission]):
        self.required_permissions = set(required_permissions)

    def __call__(self, group_id: str, user: User = Depends(get_current_user)):
        # If group_id is None, check across all of the user's groups.
        if group_id is None:
            held = set()
            for perm in user.permissions:
                held.update(perm.permission)
            if held & self.required_permissions:
                return user
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        for perm in user.permissions:
            if str(perm.group_id) == group_id:
                if self.required_permissions & set(perm.permission):
                    return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient group permissions",
        )


async def from_title_id(title_id: str, db=Depends(get_db)):
    """Group id provider: Fetch group ID from title ID."""
    if not ObjectId.is_valid(title_id):
        raise HTTPException(400, f"ID '{title_id}' is not a valid ObjectId")

    title = await db.titles.find_one({"_id": ObjectId(title_id)})
    if not title or "group_id" not in title:
        raise HTTPException(404, "Title not found")
    return str(title["group_id"])


async def from_external_id(external_id: str, db=Depends(get_db)):
    """Group id provider: Fetch group ID from external ID."""
    title = await db.titles.find_one({"external_id": external_id})
    if not title or "group_id" not in title:
        raise HTTPException(404, "Title not found")
    return str(title["group_id"])


async def from_group_id(group_id: str):
    """Group id provider: Pass through group ID."""
    return group_id


async def in_any_group():
    """Group id provider: Pass through any group ID from user permissions."""
    return None  # This will be handled in the guard to check all groups


def require_group_permission(
    required_permission: Permission,
    group_id_provider,
):
    """Dependency to check if user belongs to a group and has sufficient permissions.
    Args:
        required_permission (Permission): The required permission level.
        group_id_provider: A dependency that provides the group ID.

    Returns:
        A dependency that raises HTTPException if the user lacks permission.
    """
    guard = GroupGuard(required_permission)

    async def dep(
        group_id: str = Depends(group_id_provider),
        user: User = Depends(get_current_user),
    ) -> User:
        return guard(group_id=group_id, user=user)

    return dep


def require_group_any_permission(
    required_permissions: list[Permission],
    group_id_provider,
):
    """Dependency that passes if the user holds ANY of ``required_permissions`` in the group.

    Args:
        required_permissions (list[Permission]): Permissions, any of which suffices.
        group_id_provider: A dependency that provides the group ID.

    Returns:
        A dependency that raises HTTPException if the user lacks all permissions.
    """
    guard = GroupAnyGuard(required_permissions)

    async def dep(
        group_id: str = Depends(group_id_provider),
        user: User = Depends(get_current_user),
    ) -> User:
        return guard(group_id=group_id, user=user)

    return dep


def require_role(required_role: Role):
    """Dependency to check if user has the required role.
    Args:
        required_role (Role): The required role (e.g., Role.admin).

    Returns:
        A dependency that raises HTTPException if the user lacks the required role.
    """
    guard = RoleGuard(required_role)

    async def dep(
        user: User = Depends(get_current_user),
    ) -> User:
        return guard(user=user)

    return dep


def require_task_state(required_states: list[str], external_id_provider=False):
    """Dependency to check if a title is in one of the required states.
    Args:
        required_states (list[str]): List of allowed states (e.g., ["ready", "user_approved"]).
        id_provider: The name of the field to use as the title ID (default is "_id").

    Returns:
        A dependency that raises HTTPException if the title is not in an allowed state.
    """

    async def dep(
        title_id: str,
        db=Depends(get_db),
    ) -> str:
        current_title = await db.titles.find_one({"_id": ObjectId(title_id)})
        current_state = current_title["state"]
        if current_state not in required_states:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Title state '{current_state}' does not allow this operation",
            )
        return current_state

    async def external_dep(
        external_id: str,
        db=Depends(get_db),
    ) -> str:
        current_title = await db.titles.find_one({"external_id": external_id})
        current_state = current_title["state"]
        if current_state not in required_states:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Title state '{current_state}' does not allow this operation",
            )
        return current_state

    if external_id_provider:
        return external_dep
    return dep
