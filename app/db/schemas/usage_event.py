"""Usage events sent by the frontend for product analytics.

Privacy contract: events carry ``user_id`` so that "latest snapshot per user"
and distinct-user counts can be computed, but every ``/stats`` endpoint only
returns aggregates per group / model / time bucket. There is deliberately no
per-user read endpoint.
"""

import json
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.schemas.base import BaseModelWithId, ObjectIdField

# Frontend sends one heartbeat every HEARTBEAT_SECONDS while the editor tab is
# visible; session duration = heartbeats * HEARTBEAT_SECONDS.
HEARTBEAT_SECONDS = 30

MAX_PAYLOAD_KEYS = 32
MAX_PAYLOAD_LIST_ITEMS = 32
MAX_PAYLOAD_BYTES = 2048
MAX_EVENTS_PER_BATCH = 200


class UsageEventType(str, Enum):
    editor_open = "editor_open"
    editor_heartbeat = "editor_heartbeat"
    editor_close = "editor_close"
    settings_snapshot = "settings_snapshot"
    shortcut = "shortcut"
    mouse_action = "mouse_action"
    filter_change = "filter_change"
    save = "save"
    reset_scan = "reset_scan"
    reset_title = "reset_title"
    predictions_toggled = "predictions_toggled"


# Keys a payload must contain for a given event type (value type checked too).
REQUIRED_PAYLOAD_KEYS: dict[UsageEventType, dict[str, type | tuple[type, ...]]] = {
    UsageEventType.shortcut: {"action": str},
    UsageEventType.mouse_action: {"action": str},
    UsageEventType.filter_change: {"filter": str, "value": str},
    UsageEventType.editor_heartbeat: {"active": bool},
}

_SCALAR = (str, int, float, bool, type(None))


class UsageEventIn(BaseModel):
    """Event as sent by the client; identity fields and ``ts`` are set server-side."""

    type: UsageEventType
    client_ts: datetime | None = None
    session_id: str = Field(min_length=8, max_length=64)
    title_id: str | None = None
    payload: dict = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, payload: dict) -> dict:
        if len(payload) > MAX_PAYLOAD_KEYS:
            raise ValueError(f"payload has more than {MAX_PAYLOAD_KEYS} keys")
        for key, value in payload.items():
            if not isinstance(key, str) or len(key) > 64:
                raise ValueError("payload keys must be strings of at most 64 chars")
            if isinstance(value, list):
                if len(value) > MAX_PAYLOAD_LIST_ITEMS or not all(
                    isinstance(v, _SCALAR) for v in value
                ):
                    raise ValueError(f"payload list '{key}' too long or not scalar")
            elif not isinstance(value, _SCALAR):
                raise ValueError(f"payload value '{key}' must be scalar or list")
        if len(json.dumps(payload, default=str)) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload larger than {MAX_PAYLOAD_BYTES} bytes")
        return payload

    @model_validator(mode="after")
    def validate_required_keys(self):
        required = REQUIRED_PAYLOAD_KEYS.get(self.type, {})
        for key, expected in required.items():
            if key not in self.payload:
                raise ValueError(f"payload for '{self.type.value}' requires '{key}'")
            if not isinstance(self.payload[key], expected):
                raise ValueError(f"payload '{key}' has wrong type")
        if self.type == UsageEventType.settings_snapshot and not self.payload:
            raise ValueError("settings_snapshot payload must not be empty")
        return self


class UsageEvent(BaseModelWithId):
    """Stored event document (collection ``usage_events``)."""

    type: UsageEventType
    ts: datetime = Field(default_factory=datetime.now)  # server receive time
    client_ts: datetime | None = None
    user_id: ObjectIdField
    group_id: ObjectIdField | None = None
    title_id: ObjectIdField | None = None
    session_id: str
    payload: dict = Field(default_factory=dict)
