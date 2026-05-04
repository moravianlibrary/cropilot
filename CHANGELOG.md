# 1.2.0 - 2026-04-05

## Added

New version contains improved hatchet task code, as well as preparation of Settings object passed into the task, which will enable custom processing per group.

## Changes

### Settings

- Hatchet tasks are now spawned with a Settings object. Settings contain info about used models and are a prerequisite to run "custom" tasks per group.
- To create a group/title with custom model, use `.settings.crop_model` instead of `.model`.
- For backward compatibility, run [this](https://github.com/moravianlibrary/orezy-backend/blob/main/deploy/scripts/mongo_update1.2.0.js) Mongo script before deploy.

### Faster image loading

- Reworked integration pipeline so Cropilot does not use full resolution images and keeps its own copy of JPGs with max size of 1400px. These copies are created in /integration/create endpoint and cleaned up in /integration/complete. They live in SCANS_VOLUME_PATH directory.
- Merged RETRAIN_ and SCANS_ volume to one, **RETRAIN_VOLUME_PATH env variable is now deprecated**.



# 1.1.0 - 2026-04-03

## Added

The version introduces a user system. Every user now has their own account which gives them permissions to see specific content. The new structure consists of groups and titles. Titles now belong to groups. User can gain access to title by gaining permission to the group. Users can be in multiple groups and have different permission in each one. Lastly, admin user manages the system by adding new groups, users and permissions. Authentication is possible to user account via username-password combination, or via API key for API requests.

## Changes

### API
- Created endpoints for `users`, `groups` and `models`.
- Renamed `/ndk` endpoints to `/integration`.
- Updated `/integration/create` endpoint: new titles must be added into a group (`/integration/create?group_id=ID`), optional parameters (crop_type_code, page_count...) are now accepted in `metadata{*}` parameter.
- Updated all title `create` endpoints: `crop_method` was renamed to `model`, list of available models is now under `/models` endpoint. When not filled, result falls back to default model of the group.
- Reworked authorization (see below).

### Security
- Added new user system with groups, roles, and permissions. Before accessing the web editor, the user is now prompted to login and authentitate via JWT.
- All titles belong to a specific group. User can have 4 permissions in the group: **read-title** (can open web editor for titles in a group), **read-group** (can open list of titles belonging to a group), **write** (can save changes in the editor), **upload** (can upload new titles to a group). Permissions are set by administator.
- Administrator is a new role which is able to access every endpoint. Admins can create or delete groups, add users to groups, create new users. Admin has automatically every permission in all groups.
- During deployment, a default admin user will be created based on environment variables (`ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_NAME`). New admin will be recreated if email variable changes.
- Removed static `WEBAPP_TOKEN` variable, the token is replaced with group-based API keys.
- API key is automatically created per group and can be utilized by adding a header (`{"X-API-Key": YOUR-API-KEY}`) to the requests. The key allows to send API requests for operations related to the group. Key is accessible only by administrator which can also revoke and manage them.
- All endpoints return 403 when an unauthorized request is attempted.


### Database
- Updated MongoDB version from 6 to 8.2 (follow https://www.mongodb.com/docs/manual/release-notes/8.0-upgrade/ to perform the 2 major upgrades)
- Added entities users and groups. All current titles which are not assigned to a group will become unreachable. A Mongo script below shows how to assign them back, after a group is created.
```
const myGroupId = ObjectId("<NEW GROUP ID>")
const missingTitleIds = db.titles.find(
  {
    $or: [
      { group_id: { $exists: false } },
      { group_id: null }
    ]
  },
  { _id: 1 }
).toArray().map(t => t._id)
db.groups.updateOne(
  { _id: myGroupId },
  { $set: { title_ids: allTitleIds } }
)
db.titles.updateMany(
  { group_id: { $exists: false } },
  { $set: { group_id: myGroupId } }
)
```

### Models
- Users can now select from multiple crop models when creating new title.
- Models are stored on a **separate** volume (`models:` in docker compose).
- A default model is added onto the volume during the first deploy.

### Monitoring
- Enabled Prometheus exporting for API, metrics are available via API at `/metrics`. A sample prometheus exporter config can be found under `deploy/`.

### Deployment change summary
- Added a new environment variable `PWD_SECRET` used for signing JWT (we recommend to generate secret with length 256).
- Added variables for creating an admin user `ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_NAME`.
- Removed static `WEBAPP_TOKEN` variable, the token is replaced with group-based API keys.
- Added PVC for models/
- Added required build arguments `RETRAIN_VOLUME_PATH, MODELS_VOLUME_PATH, SCANS_VOLUME_PATH` (they were previously used only as env. variables).
- Renamed frontend image to trinera/cropilot-frontend.

# 1.0.0 - 2025-10-12

The initial release includes core application functionality. User can upload a set of scans for which the application predicts crop coordinates. User is then able to review and edit the predictions in a web app, save the changes and get the crop instructions in a JSON format. The app architecture consists of task queue, API, database, and 2 pretrained models.

### Hatchet task queue

Task queue has these workflows:
- `autocrop` Main processing workflow which (in 3 steps) predicts position and rotation of page bounding boxes
in a title.
- `maintenance` A cron job scheduled for 2 AM every night, saves a copy of the current state of database to disk.

### API

A RestAPI interface providing endpoints for:
- `/ndk` endpoints for API integration
- `/titles` endpoints for frontend web editor

### Database

- A NoSQL MongoDB database with Titles collection. Stores crop predictions and edits. Export of this collection
serves as instructions for scan crop softwares.

### Models

- Models are utilized by Hatchet workers. First model predicts the number and position of bounding boxes, second one predicts their rotation.
