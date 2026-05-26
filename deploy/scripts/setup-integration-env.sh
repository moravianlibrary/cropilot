#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-}"

if [[ -z "$API_URL" ]]; then
  echo "ERROR: API_URL is not set" >&2
  exit 1
fi

if [[ -z "${ADMIN_EMAIL:-}" ]]; then
  echo "ERROR: ADMIN_EMAIL is not set" >&2
  exit 1
fi

if [[ -z "${ADMIN_PASSWORD:-}" ]]; then
  echo "ERROR: ADMIN_PASSWORD is not set" >&2
  exit 1
fi

echo "Logging in as admin..." >&2

LOGIN_RESPONSE=$(curl -sS -X POST "$API_URL/users/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASSWORD")

TOKEN=$(echo "$LOGIN_RESPONSE" | jq -r '.access_token')

if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "ERROR: Failed to login, response was:" >&2
  echo "$LOGIN_RESPONSE" >&2
  exit 1
fi

echo "Logged in as admin" >&2

echo "Creating group..." >&2

GROUP_RESPONSE=$(curl -sS -X POST "$API_URL/groups" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Integration",
    "description": "A default group for integration deployment"
  }')

GROUP_ID=$(echo "$GROUP_RESPONSE" | jq -r '.id')
GROUP_API_KEY=$(echo "$GROUP_RESPONSE" | jq -r '.api_key')

echo "Group created, outputing variables GROUP_ID=$GROUP_ID and GROUP_API_KEY=$GROUP_API_KEY" >&2

if [[ -z "$GROUP_ID" || "$GROUP_ID" == "null" ]]; then
  echo "ERROR: Failed to create group, response was:" >&2
  echo "$GROUP_RESPONSE" >&2
  exit 1
fi
