import logging
from app.tasks.hatchet_client import hatchet
from app.tasks.workflows.integration_workflow import prepare_data_workflow
from app.tasks.workflows.smartcrop_workflow import autocrop_workflow
from app.tasks.workflows.maintenance import maintenance_task


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    worker = hatchet.worker(
        "crop-worker", slots=1, workflows=[autocrop_workflow, prepare_data_workflow, maintenance_task]
    )
    worker.start()


if __name__ == "__main__":
    main()
