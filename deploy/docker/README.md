# Docker Deployment

Cropilot can be deployed using the provided Docker Compose setup. The stack consists of the following services:

- Hatchet queue manager
- Hatchet worker
- API service
- MongoDB
- Angular frontend

For more information about Hatchet, see the official documentation:  
https://docs.hatchet.run/v1

---

# Production Deployment

The production setup is defined in `docker-compose.yml`.

Before starting the application, configure the following environment files, and place them at the same level as compose file:

## 1. `.env`

Create this file from `.env.example` and replace all placeholder values with your own secure credentials.

## 2. `.admin-user-env`

Create this file from `.admin-user-env.example`.

This file defines the default administrator credentials used for:

- Hatchet Dashboard login
- Cropilot administrator account

The Cropilot admin account is required to create additional users.

## 3. `.worker-env`

This file contains the Hatchet API token used by the worker.

Generate it automatically by running:

```bash
./deploy/scripts/generate-worker-env.sh
```

Alternatively, create it manually from `.worker-env.example` and replace the token value with your Hatchet API token.

You can generate a token in the Hatchet Dashboard under:

```text
Settings → API Tokens → Generate API Token
```

---

## Starting the Production Stack

Run all services in detached mode:

```bash
docker-compose up -d
```

This starts:

- Hatchet server
- PostgreSQL
- RabbitMQ
- MongoDB
- Cropilot API
- Cropilot worker
- Angular frontend

## Available Services

| Service | URL |
|---|---|
| Cropilot App | http://127.0.0.1:1234 |
| API Swagger Docs | http://127.0.0.1:8000/docs |
| Hatchet Dashboard | http://127.0.0.1:8888 |

---

# Local Development Setup

For local development, individual application components can be run separately while Hatchet services are managed through Docker Compose.

Use the `docker-compose.hatchet-local.yml` configuration file to start only the Hatchet-related infrastructure.

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

> Note: The Hatchet token can be generated in the Hatchet Dashboard or by running `generate-worker-env.sh`.

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