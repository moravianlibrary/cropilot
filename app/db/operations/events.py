import logging
from datetime import datetime

from bson import ObjectId

from app.db.schemas.usage_event import UsageEvent, UsageEventIn
from app.db.schemas.user import User

logger = logging.getLogger(__name__)


async def store_events(events: list[UsageEventIn], user: User, db) -> int:
    """Persist a batch of client events.

    ``user_id`` always comes from the authenticated user, never from the
    client. ``group_id`` is resolved from the referenced title with one query
    per batch; an unknown title (e.g. deleted mid-session) keeps the event but
    leaves ``group_id`` empty instead of failing the whole batch.
    """
    if not events:
        return 0

    title_ids = {
        ObjectId(e.title_id)
        for e in events
        if e.title_id and ObjectId.is_valid(e.title_id)
    }
    group_by_title: dict[ObjectId, ObjectId | None] = {}
    if title_ids:
        titles = await db.titles.find(
            {"_id": {"$in": list(title_ids)}}, {"group_id": 1}
        ).to_list(length=None)
        group_by_title = {t["_id"]: t.get("group_id") for t in titles}

    now = datetime.now()
    docs = []
    for e in events:
        title_id = (
            ObjectId(e.title_id)
            if e.title_id and ObjectId.is_valid(e.title_id)
            else None
        )
        doc = UsageEvent(
            type=e.type,
            ts=now,
            client_ts=e.client_ts,
            user_id=user.id,
            group_id=group_by_title.get(title_id) if title_id else None,
            title_id=title_id,
            session_id=e.session_id,
            payload=e.payload,
        )
        docs.append(doc.model_dump(by_alias=True))

    await db.usage_events.insert_many(docs, ordered=False)
    logger.debug(f"Stored {len(docs)} usage events for {user.email}")
    return len(docs)
