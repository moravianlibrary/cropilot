import logging
import os

from app.tasks.hatchet_client import hatchet


from hatchet_sdk import (
    Context,
    EmptyModel,
)


logger = logging.getLogger(__name__)
maintenance_task = hatchet.workflow(
    name="maintenance", on_crons=["0 2 * * *"]
)  # Runs once 2 am daily

SCANS_VOLUME_PATH = os.getenv("SCANS_VOLUME_PATH")
MONGODB_URI = os.getenv("MONGODB_URI")


@maintenance_task.task()
def mongodump(input: EmptyModel, ctx: Context) -> dict[str, str]:
    """Performs a MongoDB dump for maintenance purposes."""
    ctx.log("Starting MongoDB dump.")

    location = os.path.join(SCANS_VOLUME_PATH, "mongodump")

    ret = os.system(f"mongodump --uri={MONGODB_URI} --out={location} --gzip")
    if ret != 0:
        msg = f"mongodump exited with code {ret}"
        logger.error(msg)
        ctx.log(msg)
        raise RuntimeError(msg)

    ctx.log("Mongodump completed successfully.")

    return {
        "status": "success",
        "location": location,
    }
