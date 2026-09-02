from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.db.schemas.base import BaseModelWithId, ObjectIdField


class Settings(BaseModel):
    crop_model: str = "default"
    rotation_model: str = "text"


class Anomaly(str, Enum):
    page_count_mismatch = "page_count_mismatch"
    low_confidence = "low_confidence"
    dimensions = "odd_dimensions"
    prediction_error = "no_prediction"
    prediction_overlap = "prediction_overlap"
    split = "bad_split"


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
        values.angle = round(angle, 2)
        return values


class Scan(BaseModelWithId):
    filename: str
    scan_name: str
    predicted_pages: list[Page] = Field(default_factory=list)
    user_edited_pages: list[Page] | None = None
    orientation: Literal[0, 90, 180, 270] = 0


class ScanUpdate(BaseModelWithId):
    pages: list[Page] | None = None
    orientation: Literal[0, 90, 180, 270] | None = None


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


class TitleAssign(BaseModel):
    """Body for assigning a title to a user (``None`` clears the assignment)."""

    user_id: str | None = None


class ReviewStats(BaseModel):
    """Aggregated comparison of ML predictions vs. user edits for one title.

    Computed by ``app.core.review_metrics.compute_review_stats`` whenever the
    user saves edits, and cleared when predictions are reset or re-run.
    """

    scans_total: int
    scans_edited: int  # scans where user_edited_pages is not None
    scans_changed: int  # scans where the geometry actually differs
    edit_ratio: float  # scans_edited / scans_total (drives the retrain rule)
    pages_predicted: int
    pages_edited: int
    pages_added: int
    pages_removed: int
    pairs_matched: int
    mean_iou: float | None = None
    mean_center_shift: float | None = None  # normalized euclidean distance
    mean_width_delta: float | None = None  # signed, edited - predicted
    mean_height_delta: float | None = None
    mean_angle_delta: float | None = None  # absolute degrees
    orientation_changed: int  # scans with orientation != 0
    crop_model: str | None = None  # copied from settings at compute time
    rotation_model: str | None = None
    computed_at: datetime = Field(default_factory=datetime.now)


class Title(BaseModelWithId):
    external_id: str | None = None
    filelist: list[str] = Field(default_factory=list)
    settings: Settings | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    modified_at: datetime = Field(default_factory=datetime.now)
    modified_by: str | None = None
    assigned_to: ObjectIdField | None = None
    state: TaskState = Field(default=TaskState.new)
    scans: list[Scan] = Field(default_factory=list)

    # Review lifecycle timestamps (None until the transition happens).
    ready_at: datetime | None = None  # worker finished predictions
    user_approved_at: datetime | None = None  # first user save
    completed_at: datetime | None = None  # integration marked completed/retrain
    review_stats: ReviewStats | None = None

    group_id: ObjectIdField | None = None

    metadata: dict | None = None
