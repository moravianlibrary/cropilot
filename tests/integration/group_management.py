import os
import uuid
import requests


BASE_URL = os.getenv("CROPILOT_API_URL", "http://localhost:8000").rstrip("/")

API_KEY = os.getenv("CROPILOT_API_KEY")
TOKEN = os.getenv("CROPILOT_TOKEN")


def login_for_token():
    response = requests.post(
        f"{BASE_URL}/users/login",
        data={
            "username": os.environ["ADMIN_EMAIL"],
            "password": os.environ["ADMIN_PASSWORD"],
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def headers():
    if API_KEY:
        return {"X-API-Key": API_KEY}
    if TOKEN:
        return {"Authorization": f"Bearer {TOKEN}"}
    raise RuntimeError("Set CROPILOT_API_KEY or CROPILOT_TOKEN")


def assert_ok(response):
    assert response.status_code == 200, response.text
    return response.json() if response.content else None


def get_id(data, *keys):
    for key in keys:
        if isinstance(data, dict) and key in data:
            return data[key]
    raise AssertionError(f"Could not find id in response: {data}")


TOKEN = login_for_token()


def test_groups_and_users_flow():
    unique = uuid.uuid4().hex[:8]

    group_id = None
    user_id = None

    test_email = f"pytest-user-{unique}@example.com"

    try:
        # POST /groups
        create_group_resp = requests.post(
            f"{BASE_URL}/groups",
            json={
                "name": f"Pytest group {unique}",
                "description": "Group for groups/users integration testing",
                "default_settings": {
                    "crop_model": "default",
                    "rotation_model": "rotate-300e-best",
                },
            },
            headers=headers(),
            timeout=30,
        )
        group = assert_ok(create_group_resp)
        group_id = get_id(group, "group_id", "_id", "id")
        print(f"Created group with ID: {group_id}")

        # GET /groups
        groups_resp = requests.get(
            f"{BASE_URL}/groups",
            headers=headers(),
            timeout=30,
        )
        groups = assert_ok(groups_resp)
        print(f"Retrieved groups: {groups}")

        # GET /groups/{group_id}
        group_titles_resp = requests.get(
            f"{BASE_URL}/groups/{group_id}",
            headers=headers(),
            timeout=30,
        )
        assert_ok(group_titles_resp)
        print(f"Retrieved titles for group ID: {group_id}")

        # PATCH /groups/{group_id}
        update_group_resp = requests.patch(
            f"{BASE_URL}/groups/{group_id}",
            json={
                "name": f"Updated pytest group {unique}",
                "description": "Updated group description",
                "default_settings": {
                    "crop_model": "default",
                    "rotation_model": "model",
                },
            },
            headers=headers(),
            timeout=30,
        )
        assert_ok(update_group_resp)
        print(f"Updated group ID: {group_id}")

        # POST /users/register
        create_user_resp = requests.post(
            f"{BASE_URL}/users/register",
            json={
                "email": test_email,
                "full_name": f"Pytest User {unique}",
                "role": "user",
            },
            headers=headers(),
            timeout=30,
        )
        user = assert_ok(create_user_resp)
        user_id = get_id(user, "user_id", "_id", "id")
        print(f"Created user with ID: {user_id}")

        # GET /users/current-user
        current_user_resp = requests.get(
            f"{BASE_URL}/users/current-user",
            headers=headers(),
            timeout=30,
        )
        assert_ok(current_user_resp)
        print("Retrieved current user")

        # GET /users/current-user?title_id=...
        # This endpoint supports title_id, but users/groups flow does not create a title.
        # So we skip title_id here to keep the test isolated.

        # GET /users
        users_resp = requests.get(
            f"{BASE_URL}/users",
            headers=headers(),
            timeout=30,
        )
        users = assert_ok(users_resp)
        print(f"Retrieved users: {users}")

        # GET /users?group_id=...
        group_users_resp = requests.get(
            f"{BASE_URL}/users",
            params={"group_id": group_id},
            headers=headers(),
            timeout=30,
        )
        assert_ok(group_users_resp)
        print(f"Retrieved users for group ID: {group_id}")

        # GET /users/{user_id}
        get_user_resp = requests.get(
            f"{BASE_URL}/users/{user_id}",
            headers=headers(),
            timeout=30,
        )
        assert_ok(get_user_resp)
        print(f"Retrieved user ID: {user_id}")

        # PATCH /users/{user_id}
        update_user_resp = requests.patch(
            f"{BASE_URL}/users/{user_id}",
            json={
                "email": test_email,
                "full_name": f"Updated Pytest User {unique}",
                "role": "user",
            },
            headers=headers(),
            timeout=30,
        )
        assert_ok(update_user_resp)
        print(f"Updated user ID: {user_id}")

        # PATCH /users/{user_id}/reset-password
        reset_password_resp = requests.patch(
            f"{BASE_URL}/users/{user_id}/reset-password",
            headers=headers(),
            timeout=30,
        )
        assert_ok(reset_password_resp)
        print(f"Reset password for user ID: {user_id}")

        # POST /groups/{group_id}/members
        add_members_resp = requests.post(
            f"{BASE_URL}/groups/{group_id}/members",
            json=[
                {
                    "user_id": user_id,
                    "user_permissions": [
                        "read_group",
                        "read_title",
                        "write",
                    ],
                }
            ],
            headers=headers(),
            timeout=30,
        )
        assert_ok(add_members_resp)
        print(f"Added user ID: {user_id} to group ID: {group_id}")

        # PATCH /groups/{group_id}/members
        update_members_resp = requests.patch(
            f"{BASE_URL}/groups/{group_id}/members",
            json=[
                {
                    "user_id": user_id,
                    "user_permissions": [
                        "read_group",
                        "read_title",
                        "write",
                        "upload",
                    ],
                }
            ],
            headers=headers(),
            timeout=30,
        )
        assert_ok(update_members_resp)
        print(f"Updated permissions for user ID: {user_id}")

        # DELETE /groups/{group_id}/members
        remove_members_resp = requests.delete(
            f"{BASE_URL}/groups/{group_id}/members",
            json=[user_id],
            headers=headers(),
            timeout=30,
        )
        assert_ok(remove_members_resp)
        print(f"Removed user ID: {user_id} from group ID: {group_id}")

        # POST /groups/{group_id}/api-key
        api_key_resp = requests.post(
            f"{BASE_URL}/groups/{group_id}/api-key",
            headers=headers(),
            timeout=30,
        )
        assert_ok(api_key_resp)
        print(f"Revoked/recreated API key for group ID: {group_id}")

        # GET /users?group_id=... to verify user is removed from group
        verify_group_users_resp = requests.get(
            f"{BASE_URL}/users",
            params={"group_id": group_id},
            headers=headers(),
            timeout=30,
        )
        verify_group_users = assert_ok(verify_group_users_resp)
        assert all(user["_id"] != user_id for user in verify_group_users), (
            "User still in group after removal"
        )
        print(f"Verified user ID: {user_id} is removed from group ID: {group_id}")

    finally:
        if user_id:
            delete_user_resp = requests.delete(
                f"{BASE_URL}/users/{user_id}",
                headers=headers(),
                timeout=30,
            )
            assert delete_user_resp.status_code == 204, delete_user_resp.text
            print(f"Deleted user ID: {user_id}")

        if group_id:
            delete_group_resp = requests.delete(
                f"{BASE_URL}/groups/{group_id}",
                headers=headers(),
                timeout=30,
            )
            assert_ok(delete_group_resp)
            print(f"Deleted group ID: {group_id}")

    return True


assert test_groups_and_users_flow()
