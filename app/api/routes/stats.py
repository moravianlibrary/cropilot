"""Admin-only product usage statistics. Aggregates only; see operations/stats.py."""

from datetime import datetime
from typing import Annotated, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.authz import require_role
from app.api.limiter import limiter
from app.api.setup_db import get_db
from app.db.operations import stats as ops
from app.db.operations.stats import GroupBy, StatsFilter, make_filter
from app.db.operations.stats_snapshots import get_snapshot
from app.db.schemas.user import Role

router = APIRouter(
    prefix="/stats",
    tags=["Stats"],
    dependencies=[Depends(require_role(Role.admin))],
)


def stats_filter(
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    group_id: str | None = None,
    crop_model: str | None = None,
    rotation_model: str | None = None,
) -> StatsFilter:
    try:
        return make_filter(from_, to, group_id, crop_model, rotation_model)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _envelope(f: StatsFilter, group_by: str | None, items: list[dict]) -> dict:
    return {
        "from": f.from_,
        "to": f.to,
        "group_by": group_by,
        "items": items,
    }


@limiter.limit("60/minute")
@router.get("/overview")
async def get_overview(
    request: Request,
    f: Annotated[StatsFilter, Depends(stats_filter)],
    db=Depends(get_db),
):
    """Headline counts: titles per state, scans, active users, editor time."""
    data = await ops.overview(f, db)
    return {"from": f.from_, "to": f.to, **data}


@limiter.limit("60/minute")
@router.get("/review-quality")
async def get_review_quality(
    request: Request,
    f: Annotated[StatsFilter, Depends(stats_filter)],
    group_by: GroupBy = "group",
    db=Depends(get_db),
):
    """How much users had to correct predictions, per group / model / month."""
    return _envelope(f, group_by, await ops.review_quality(f, group_by, db))


@limiter.limit("60/minute")
@router.get("/anomalies")
async def get_anomalies(
    request: Request,
    f: Annotated[StatsFilter, Depends(stats_filter)],
    group_by: GroupBy | None = None,
    db=Depends(get_db),
):
    """Anomaly flag frequency and how well flags predict user edits."""
    return _envelope(f, group_by, await ops.anomalies(f, group_by, db))


@limiter.limit("60/minute")
@router.get("/editor-usage")
async def get_editor_usage(
    request: Request,
    f: Annotated[StatsFilter, Depends(stats_filter)],
    group_by: Literal["group", "month"] = "group",
    db=Depends(get_db),
):
    """Editor sessions, time spent, keyboard vs. mouse, shortcuts, filters."""
    return _envelope(f, group_by, await ops.editor_usage(f, group_by, db))


@limiter.limit("60/minute")
@router.get("/settings")
async def get_settings(
    request: Request,
    group_id: str | None = None,
    db=Depends(get_db),
):
    """Distribution of editor settings over the latest snapshot of each user."""
    gid = None
    if group_id:
        if not ObjectId.is_valid(group_id):
            raise HTTPException(400, "Invalid group_id")
        gid = ObjectId(group_id)
    return await ops.settings_distribution(gid, db)


@limiter.limit("60/minute")
@router.get("/latest")
async def get_latest(request: Request, db=Depends(get_db)):
    """Statistics as last stored by the nightly maintenance cron (trailing 30 days)."""
    doc = await get_snapshot(db)
    if doc is None:
        raise HTTPException(404, "No statistics snapshot stored yet")
    return doc
