import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.authn import get_current_user
from app.api.limiter import limiter
from app.api.setup_db import get_db
from app.db.operations.events import store_events
from app.db.schemas.usage_event import MAX_EVENTS_PER_BATCH, UsageEventIn
from app.db.schemas.user import User

router = APIRouter(prefix="/events", tags=["Events"])
logger = logging.getLogger(__name__)

# Synthetic identities from app.api.authn that must not produce events.
NON_INTERACTIVE_USERS = {"api@request.user", "public@user.cropilot"}
MAX_BODY_BYTES = 1024 * 1024


# Generous limit: the limiter is keyed by client IP and all users behind the
# ingress share one address. The frontend batches events client-side.
@limiter.limit("600/minute")
@router.post("", status_code=202)
async def post_events(
    request: Request,
    events: Annotated[list[UsageEventIn], Body(max_length=MAX_EVENTS_PER_BATCH)],
    current_user: Annotated[User, Depends(get_current_user)],
    db=Depends(get_db),
):
    """Accepts a batch of frontend usage events for the signed-in user."""
    if current_user.email in NON_INTERACTIVE_USERS:
        raise HTTPException(403, "Usage events require a signed-in user")

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(413, "Event batch too large")

    accepted = await store_events(events, current_user, db)
    return {"accepted": accepted}
