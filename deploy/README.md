# Deployment

The project is built using uv and package-managed by ruff. Each instance is containerized into Docker containers. This repo offers 2 types of deployment:

## Local

To run your application locally, use docker-compose.hatchet-local.yml. Prerequisites of running app locally include setting up your MongoDB instance (e.g. on https://www.mongodb.com/products/platform/atlas-database) and creating .env file. Your environment file should have this structure:

.env
```
MONGODB_URI = Url to your MongoDB instance
MONGODB_PASS = Create your DB password
MONGODB_DB = DB name
ENABLE_TLS = true

SCANS_VOLUME_PATH = Local directory path where new images will be uploaded via API
RETRAIN_VLUME_PATH = Local directory where images marked for retraining will be saved
MODELS_VLUME_PATH = Local directory for storing ML models.

HATCHET_CLIENT_TLS_STRATEGY = none
HATCHET_CLIENT_TOKEN = Hatchet token. Can be acessed via Hatchet web instance, or created with generate-worker-env.sh

PWD_SECRET = JWT secret used to compare credentials
ADMIN_EMAIL = Your login username for the API
ADMIN_PASSWORD = Your login password for the API
ADMIN_NAME = Name of the user
```

Then, run the application in following steps:

- `docker-compose -f docker-compose.hatchet-local.yml up -d` Start required services (Hatchet server, PostgreSQL and RabbitMQ as Hatchet backend)
- `uv run --env-file .env fastapi dev`  Start API locally, Swagger UI will be available at http://127.0.0.1:8000/docs
- `uv run --env-file .env -m app.tasks.worker`  Start worker locally. The UI is available at http://127.0.0.1:8888/

## Production

The production version is stored in docker-compose.yml, and uses 3 environment files in total, which need to be set accordingly:

- *.env*: A password file. Create this file from .env.example, replace with your generated passwords.
- *.admin-user-env*: Default login credentials for Hatchet UI and Cropilot. Create it from .admin-user-env.example.
- *.worker-env*: Contains a JWT token obtained from Hatchet. Generate the file by running `generate-worker-env.sh`.

Inside docker compose itself, there are 3 volumes which need to be set: retrain, upload and models volume. The related env. and build variables then need to point to this location (SCANS_VOLUME_PATH, MODELS_VOLUME_PATH)

Finally, run the application with:

- `docker-compose up -d` Start all services (Hatchet server, PostgreSQL and RabbitMQ as Hatchet backend, MongoDB, API, and worker). API endpoints will be available at http://127.0.0.1:8000/docs , Hatchet UI at http://127.0.0.1:8888/ ,
and web UI at http://127.0.0.1:1234.
