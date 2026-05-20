import shutil
import certifi
import logging
from fastapi.security import APIKeyHeader, HTTPBearer, OAuth2PasswordBearer
from app.db.schemas.user import Maintains, Permission, User
from pymongo import AsyncMongoClient
from contextlib import asynccontextmanager
import os
from pwdlib import PasswordHash
from app.deps import settings_db

from app.db.schemas.user import Role

logger = logging.getLogger(__name__)
client: AsyncMongoClient | None = None
bearer = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/users/login", auto_error=False
)  # for user auth via JWT tokens


@asynccontextmanager
async def lifespan(app):
    global client

    client_kwargs = {
        "serverSelectionTimeoutMS": 5000,
        "uuidRepresentation": "standard",
    }
    if settings_db.tls_enabled:
        client_kwargs["tlsCAFile"] = certifi.where()

    client = AsyncMongoClient(settings_db.mongodb_uri, **client_kwargs)
    await client.admin.command("ping")

    db = get_db()
    await create_indexes(db)
    await create_admin(db)
    await create_public_user(db)
    await copy_default_models()

    yield
    await client.close()


def get_db():
    assert client is not None, "DB client not initialized"
    db = client.get_database(settings_db.mongodb_db)
    return db


async def create_indexes(db):
    """Create necessary indexes in the database."""
    logger.info("Creating database indexes...")
    await db.groups.create_index([("name", 1)], unique=True, name="unique_group_name")
    await db.users.create_index([("email", 1)], unique=True, name="unique_user_email")
    await db.users.create_index([("role", 1)], name="role_index")


async def create_admin(db):
    """Create an admin user if none exists.
    Uses ADMIN_EMAIL and ADMIN_PASSWORD env vars.
    Has all group permissions.
    """
    existing_admin = await db.users.find_one({"role": "admin"})
    if existing_admin:
        if existing_admin["email"] != os.getenv("ADMIN_EMAIL"):
            logger.info(
                f"Existing admin '{existing_admin['email']}' does not match admin env var, replacing admin user."
            )
            await db.users.delete_one({"_id": existing_admin["_id"]})
        else:
            return

    group_ids = await db.groups.distinct("_id")
    permissions = []
    for group_id in group_ids:
        permissions.append(Maintains(group_id=group_id, permission=list(Permission)))

    user = User(
        full_name=os.getenv("ADMIN_NAME"),
        email=os.getenv("ADMIN_EMAIL"),
        password=os.getenv("ADMIN_PASSWORD"),
        role=Role.admin,
        permissions=permissions,
    )
    user = user.model_dump(by_alias=True)
    user["password"] = password_hash.hash(user["password"])

    await db.users.insert_one(user)
    logger.info(
        f"Admin user '{user['email']}' created with permissions for all groups."
    )


async def create_public_user(db):
    """Create a public user if none exists.
    Has no group permissions, used for API key auth.
    """
    existing_user = await db.users.find_one({"email": "public@user.cropilot"})
    if not existing_user:
        user = User(
            full_name="Veřejný uživatel",
            email="public@user.cropilot",
            password="",
            role=Role.user,
            permissions=[],
        )
        user = user.model_dump(by_alias=True)

        await db.users.insert_one(user)
        logger.info(
            f"Public API user '{user['email']}' created with no group permissions."
        )


async def copy_default_models():
    """Copy default model to models volume if not already present."""
    # if directory crop_model is not present, create it
    crop_model_path = os.path.join(os.environ["MODELS_VOLUME_PATH"], "crop_model")
    if not os.path.exists(crop_model_path):
        os.makedirs(crop_model_path)
        logger.info(f"Models volume directory '{crop_model_path}' created.")
    if "default.pt" not in os.listdir(crop_model_path):
        source = "models/crop-yolov10s-100e-mosaic-best.pt"
        dest = os.path.join(crop_model_path, "default.pt")
        shutil.copy(source, dest)
        logger.info(f"Copied default model from '{source}' to '{dest}'")
    
    rotation_model_path = os.path.join(os.environ["MODELS_VOLUME_PATH"], "rotation_model")
    if not os.path.exists(rotation_model_path):
        os.makedirs(rotation_model_path)
        logger.info(f"Models volume directory '{rotation_model_path}' created.")
    if "text.pth" not in os.listdir(rotation_model_path):
        source = "models/rotate-300e-best.pth"
        dest = os.path.join(rotation_model_path, "text.pth")
        shutil.copy(source, dest)
        logger.info(f"Copied default model from '{source}' to '{dest}'")
