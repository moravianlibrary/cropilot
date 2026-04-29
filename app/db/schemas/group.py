from datetime import datetime
import secrets
import string
from pydantic import BaseModel, ConfigDict, Field
from app.db.schemas.base import BaseModelWithId, ObjectIdField
from app.db.schemas.title import Settings


class APIkey(BaseModel):
    key: str = Field(default_factory=lambda: APIkey.create_api_key())
    created_at: datetime = Field(default_factory=datetime.now)

    @staticmethod
    def create_api_key(length: int = 48) -> str:
        alphabet = string.ascii_letters + string.digits
        secret = "".join(secrets.choice(alphabet) for _ in range(length))
        return "-".join(secret[i : i + 8] for i in range(0, len(secret), 8))


class Group(BaseModelWithId):
    name: str
    description: str | None = None
    title_ids: list[ObjectIdField] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)
    api_key: APIkey = Field(default_factory=lambda: APIkey())
    default_settings: Settings


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    default_settings: Settings = Field(default_factory=Settings)


class GroupUpdate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str | None = None
    description: str | None = None
    default_settings: Settings | None = None
