import logging
from app.tasks.hatchet_client import hatchet
from app.tasks.workflows.preprocess_workflow import preprocess_workflow
from app.tasks.workflows.predict_workflow import predict_workflow
from app.tasks.workflows.maintenance import maintenance_task


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    worker = hatchet.worker(
        "crop-worker", slots=1, workflows=[predict_workflow, preprocess_workflow, maintenance_task]
    )
    worker.start()


if __name__ == "__main__":
    main()
