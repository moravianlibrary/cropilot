"""One-off backfill of ``Title.review_stats`` for titles edited before 1.2.6.

Usage:
    uv run --env-file .env -m app.scripts.backfill_review_stats [--dry-run]
        [--force] [--approximate-timestamps]

Idempotent: without ``--force`` only titles lacking ``review_stats`` are
touched, so re-running after a partial run is safe. ``--force`` recomputes
everything (e.g. after a change to the metric definition).

``--approximate-timestamps`` (opt-in) fills ``completed_at`` / ``user_approved_at``
from ``modified_at`` for titles already in a terminal / approved state. This is
an approximation: ``modified_at`` is the last write, not the transition time.
``ready_at`` cannot be reconstructed and is left empty, so turnaround medians
only cover titles processed after the deploy.
"""

import argparse
import logging

import certifi
from pymongo import MongoClient

from app.core.review_metrics import compute_review_stats
from app.db.schemas.title import TaskState, Title
from app.deps import settings_db

logger = logging.getLogger("backfill_review_stats")


def connect():
    kwargs = {"tlsCAFile": certifi.where()} if settings_db.tls_enabled else {}
    client = MongoClient(settings_db.mongodb_uri, **kwargs)
    return client.get_database(settings_db.mongodb_db)


def backfill(db, dry_run: bool, force: bool, approximate_timestamps: bool) -> dict:
    query: dict = {"scans.user_edited_pages": {"$ne": None}}
    if not force:
        query["review_stats"] = {"$exists": False}

    stats = {"scanned": 0, "updated": 0, "timestamps": 0, "failed": 0}
    for doc in db.titles.find(query):
        stats["scanned"] += 1
        try:
            title = Title.model_validate(doc)
            review = compute_review_stats(title)
        except Exception as e:  # keep going; report at the end
            stats["failed"] += 1
            logger.warning(f"Title {doc.get('_id')}: {e}")
            continue

        update: dict = {"review_stats": review.model_dump()}
        if approximate_timestamps:
            if title.state in (TaskState.completed, TaskState.retrain):
                if title.completed_at is None:
                    update["completed_at"] = title.modified_at
                if title.user_approved_at is None:
                    update["user_approved_at"] = title.modified_at
                stats["timestamps"] += 1
            elif (
                title.state == TaskState.user_approved
                and title.user_approved_at is None
            ):
                update["user_approved_at"] = title.modified_at
                stats["timestamps"] += 1

        logger.info(
            f"Title {title.id} ({title.external_id}): "
            f"{review.scans_edited}/{review.scans_total} edited, "
            f"mean IoU {review.mean_iou}"
        )
        if not dry_run:
            db.titles.update_one({"_id": title.id}, {"$set": update})
        stats["updated"] += 1
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="only report")
    parser.add_argument("--force", action="store_true", help="recompute existing stats")
    parser.add_argument(
        "--approximate-timestamps",
        action="store_true",
        help="fill completed_at / user_approved_at from modified_at",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = connect()
    result = backfill(db, args.dry_run, args.force, args.approximate_timestamps)
    prefix = "[dry-run] " if args.dry_run else ""
    logger.info(
        f"{prefix}scanned={result['scanned']} updated={result['updated']} "
        f"timestamps={result['timestamps']} failed={result['failed']}"
    )


if __name__ == "__main__":
    main()
