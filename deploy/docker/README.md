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

# How to

The production deployment setup is defined in `docker-compose.yml`.

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

## Starting the Cropilot Stack

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
