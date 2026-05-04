from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from datetime import datetime
from enum import Enum

from app.db.schemas.base import BaseModelWithId, ObjectIdField


class Settings(BaseModel):
    crop_model: str = "default"
    rotation_model: str = "rotate-300e-best"


class Anomaly(str, Enum):
    page_count_mismatch = "page_count_mismatch"
    low_confidence = "low_confidence"
    dimensions = "odd_dimensions"
    prediction_error = "no_prediction"
    prediction_overlap = "prediction_overlap"


class TaskState(str, Enum):
    new = "new"
    scheduled = "scheduled"
    in_progress = "in_progress"
    ready = "ready"
    failed = "failed"
    user_approved = "user_approved"
    retrain = "retrain"
    completed = "completed"


class Page(BaseModelWithId):
    xc: float
    yc: float
    width: float
    height: float
    confidence: float = 0
    angle: float = 0
    flags: list[Anomaly] = Field(default_factory=list)

    @model_validator(mode="after")
    def round_values(cls, values):
        """Round all numeric fields to 2 decimals in unnormalized form."""
        for field in ("xc", "yc", "width", "height", "confidence"):
            val = getattr(values, field, None)
            if isinstance(val, (int, float)):
                setattr(values, field, round(val, 4))

        angle = getattr(values, "angle", 0)
        setattr(values, "angle", round(angle, 2))
        return values


class Scan(BaseModelWithId):
    filename: str
    scan_name: str
    predicted_pages: list[Page] = Field(default_factory=list)
    user_edited_pages: list[Page] | None = None


class ScanUpdate(BaseModelWithId):
    pages: list[Page]


class TitleCreate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: str | None = None
    filelist: list[str] = Field(default_factory=list)
    settings: Settings | None = None
    metadata: dict | None = None


class TitleUpdate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: str | None = None
    settings: Settings | None = None


class Title(BaseModelWithId):
    external_id: str | None = None
    filelist: list[str] = Field(default_factory=list)
    settings: Settings | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)
    modified_by: str | None = None
    state: TaskState = Field(default=TaskState.new)
    scans: list[Scan] = Field(default_factory=list)

    group_id: ObjectIdField | None = None

    metadata: dict | None = None
