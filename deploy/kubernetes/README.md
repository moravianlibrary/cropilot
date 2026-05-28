# Kubernetes Deployment

Cropilot (or Kropilot in this case?) can be deployed to Kubernetes using the provided Kustomize setup. The stack consists of the following services:

- API service
- Hatchet worker
- MongoDB
- Angular frontend
- Persistent volumes for scan data and models

Hatchet is not deployed by these manifests. Install Hatchet separately before starting Cropilot.

For more information about Hatchet, see the official documentation:  
https://docs.hatchet.run/v1

---

# How to

The Kubernetes deployment setup is defined in `deploy/kubernetes`.

Before applying the manifests, configure the following files:

## 1. Hatchet

Set up Hatchet in your cluster first. You can use the (official Kubernetes installation guide)[https://docs.hatchet.run/self-hosting/kubernetes-quickstart] from the Hatchet documentation.

After Hatchet is running, update the worker connection values in `kustomization.yml`:

- `HATCHET_CLIENT_HOST_PORT`
- `HATCHET_CLIENT_TLS_STRATEGY=None`

Also set the worker token in `secret.yml`:

- `HATCHET_CLIENT_TOKEN` (in Hatchet web UI, go to Settings → API Tokens → Generate API Token)

## 2. `secret.yml`

Replace all placeholder values with your own secure credentials.

This file defines secrets used by:

- MongoDB
- Cropilot API
- Cropilot worker
- Cropilot administrator account

The Cropilot admin account is required to create additional users.

## 3. `ingress.yml` and `kustomization.yml`

Configure ingress hosts for the frontend and API.

Add TLS configuration if your cluster uses cert-manager or a pre-created TLS secret.

Then, update the public URLs for your deployment:

- `WEBAPP_FRONTEND_URL`: public URL of the frontend
- `APP_DATA_SERVER_URL`: public URL of the API service

## 4. `storage.yml`

Configure persistent storage for scan data and models.

Add `storageClassName` if your cluster requires one. The scan data and model claims use `ReadWriteMany` because the API and worker share them.

---

## Starting the Cropilot Stack

Apply the Kustomize deployment:

```bash
kubectl apply -k deploy/kubernetes
```

This starts:

- MongoDB
- Cropilot API
- Cropilot worker
- Frontend

## Local Port Forwarding

For a quick smoke test without ingress:

```bash
kubectl -n cropilot port-forward svc/frontend 1234:80
kubectl -n cropilot port-forward svc/api 8000:8000
```

| Service | URL |
|---|---|
| Cropilot App | http://127.0.0.1:1234 |
| API Swagger Docs | http://127.0.0.1:8000/docs |
