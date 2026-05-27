# Development

Cropilot is developed as a Python application managed with [`uv`](https://docs.astral.sh/uv/). The backend stack combines FastAPI, Hatchet, and MongoDB.

For local development, the application can be run as separate components. The API server and worker process run locally through `uv`, while Hatchet and its supporting services can be started with Docker Compose.

## Aplication structure

The application is organized into the following modules:

- **api**: FastAPI routers used by the frontend and external integrations.
- **core**: AI models and image-processing logic, including a fine-tuned YOLO model for page coordinate detection and a ResNet-based rotation model for orientation correction.
- **db**: MongoDB collections defined with Pydantic models, together with related database queries.
- **tasks**: Hatchet task queue configuration and worker tasks that execute the machine learning pipeline asynchronously.

## How to setup local Cropilot instance

For local development, individual application components can be run separately while Hatchet services are managed through Docker Compose.

Use the `deploy/docker-compose.hatchet-local.yml` configuration file to start only the Hatchet-related infrastructure.

## Prerequisites

Before starting, prepare:

- A MongoDB instance (for example MongoDB Atlas)
- A configured `.env` file

Example `.env` structure:

```env
MONGODB_URI=your_mongodb_connection_string
MONGODB_PASS=your_database_password
MONGODB_DB=your_database_name
ENABLE_TLS=true

SCANS_VOLUME_PATH=path_to_uploaded_images
RETRAIN_VOLUME_PATH=path_to_retraining_images
MODELS_VOLUME_PATH=path_to_ml_models

HATCHET_CLIENT_TLS_STRATEGY=none
HATCHET_CLIENT_TOKEN=your_hatchet_token

PWD_SECRET=jwt_secret
ADMIN_EMAIL=admin_login_email
ADMIN_PASSWORD=admin_login_password
ADMIN_NAME=admin_display_name
```

> Note: The Hatchet token can be generated in the Hatchet Dashboard or by running `deploy/scripts/generate-worker-env.sh`.

---

## Start Local Infrastructure

Start Hatchet and its required services:

```bash
docker-compose -f docker-compose.hatchet-local.yml up -d
```

This launches:

- Hatchet server
- PostgreSQL
- RabbitMQ

---

## Run Backend Components Locally

### Start the API server

```bash
uv run --env-file .env fastapi dev
```

Swagger UI will be available at:

```text
http://127.0.0.1:8000/docs
```

### Start the worker process

```bash
uv run --env-file .env -m app.tasks.worker
```

Hatchet Dashboard will be available at:

```text
http://127.0.0.1:8888
```
--- 

## Cheat sheet

```bash
uvx ruff format . && uvx ruff check --fix .
```

Format the project and fix linter errors.

```bash
uv run pytest -v
```

Run tests.