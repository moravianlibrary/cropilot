from datetime import datetime, timedelta, timezone
import secrets
import logging
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, Security, status
from app.api.setup_db import (
    get_db,
    password_hash,
    oauth2_scheme,
    api_key_header,
)
from app.deps import settings_api
from pydantic import BaseModel

from app.db.schemas.user import User

logger = logging.getLogger(__name__)


class Token(BaseModel):
    access_token: str
    token_type: str


def verify_password(plain_password, hashed_password):
    """Verify a plain password against its hashed version.

    Args:
        plain_password (str): The plain text password to verify.
        hashed_password (str): The hashed password to compare against.
    Returns:
        bool: password match.
    """
    return password_hash.verify(plain_password, hashed_password)


def get_password_hash(password):
    """Get the hashed version of a password.

    Args:
        password (str): The plain text password to hash.
    Returns:
        str: hashed password.
    """
    return password_hash.hash(password)


async def authenticate_user(db, email: str, password: str) -> User | bool:
    """Authenticate a user by their email and password.

    Args:
        db: Database connection.
        email (str): The user's email.
        password (str): The user's plain text password.
    Returns:
        User | bool: The authenticated user object if successful, False otherwise."""
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(password, user["password"]):
        return False
    return user


def create_access_token(
    data: dict, expires_delta_min: int = settings_api.pwd_access_token_expire_minutes
):
    """Create a JWT access token.

    Args:
        data (dict): The data to encode in the token.
        expires_delta (timedelta): The token's expiration time.
    Returns:
        str: The encoded JWT token.
    """
    to_encode = data.copy()

    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_delta_min)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings_api.pwd_secret_key, algorithm=settings_api.pwd_algorithm
    )
    return encoded_jwt


async def get_current_user(
    token: Annotated[str | None, Security(oauth2_scheme)],
    api_key: Annotated[str | None, Security(api_key_header)],
    db=Depends(get_db),
):
    """Retrieve the current user based on the provided JWT token.

    Args:
        token (str): The JWT token from the request.
        db: Database connection.
    Returns:
        User: The current authenticated user.
    Raises:
        HTTPException: If the token is invalid or the user cannot be found.
    """
    try:
        # JWT token authentication, user is logged in via app
        if token:
            payload = jwt.decode(
                token,
                settings_api.pwd_secret_key,
                algorithms=[settings_api.pwd_algorithm],
            )
            # check expiration
            if payload.get("exp") < int(datetime.now(timezone.utc).timestamp()):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
                )

            # check if user exists
            email = payload.get("sub")
            user = await db.users.find_one({"email": email})
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalid"
                )
        # API key authentication, group-based access for API users
        elif api_key:
            group_id = await auth_via_api_key(api_key, db)
            user = {
                "email": "api@request.user",
                "full_name": "API request",
                "role": "user",
                "permissions": [
                    {
                        "group_id": group_id,
                        "permission": ["upload", "read_title", "read_group"],
                    },
                ],
            }
        # No credentials provided, use public user with limited permissions
        else:
            logger.debug("No authentication credentials provided, using public user")
            user = await db.users.find_one({"email": "public@user.cropilot"})
        return User.model_validate(user)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials, " + str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def auth_via_api_key(
    raw_key: Optional[str] = Depends(api_key_header),
    db=Depends(get_db),
) -> str:
    """Authenticate using an API key and return the associated group ID.

    Args:
        raw_key (str): The raw API key provided in the request header.
        db: Database connection.
    Returns:
        str: The group ID associated with the valid API key.
    Raises:
        HTTPException: If the API key is invalid, expired, or not found.
    """
    expected = await db.groups.find_one(
        {"api_key.key": raw_key}, {"api_key.$": 1, "_id": 1}
    )
    logger.info(f"API Key access attempt for key: {raw_key[:5]}***")
    # Check if API key exists
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    # Check if valid
    if not secrets.compare_digest(raw_key, expected["api_key"]["key"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    return str(expected["_id"])
