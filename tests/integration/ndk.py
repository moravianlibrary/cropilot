import os
from time import sleep
import requests

API_URL = "http://localhost:8000"


def login_for_token():
    response = requests.post(
        f"{API_URL}/users/login",
        data={
            "username": os.environ["ADMIN_EMAIL"],
            "password": os.environ["ADMIN_PASSWORD"],
        },
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    return token


def main():
    # Login as admin
    token = login_for_token()

    # Use the token for subsequent requests
    bearer_headers = {"Authorization": f"Bearer {token}"}

    print(f"Logged in as admin, access token: {token}")

    try:
        # Create a new group
        response = requests.post(
            f"{API_URL}/groups",
            json={
                "name": "Integration Test Group",
                "description": "Group for integration testing",
            },
            headers=bearer_headers,
        )
        response.raise_for_status()
        group_id = response.json()["id"]
        group_api_key = response.json()["api_key"]

        print(f"Created group with ID: {group_id} and API Key: {group_api_key}")

        # Use api key to create a new title
        headers = {"X-API-Key": group_api_key}
        response = requests.post(
            f"{API_URL}/integration/create?group_id={group_id}",
            json={
                "filelist": [
                    "tests/sample_input/1-a0001.jpg",
                    "tests/sample_input/1-a0002.jpg",
                    "tests/sample_input/1-a0003.jpg",
                    "tests/sample_input/1-a0004.jpg",
                    "tests/sample_input/1-a0005.jpg",
                ],
            },
            headers=headers,
        )
        response.raise_for_status()
        title_id = response.json()["id"]

        print("Created title using group API key.")

        # Check status of the created title
        response = {"state": None}
        while response["state"] != "ready":
            sleep(5)
            response = requests.get(
                f"{API_URL}/integration/{title_id}/status", headers=headers
            )
            response.raise_for_status()
            response = response.json()
            print(f"Title status: {response['state']}")
        print("Title processing completed.")

        # Update crop bounding box
        response = requests.get(
            f"{API_URL}/{title_id}/scans",
            headers=headers,
        )
        response.raise_for_status()
        response = response.json()
        scan_ids = [scan["_id"] for scan in response["scans"]]
        new_coordinates = []
        for scan_id in scan_ids[:4]:
            new_coordinates.append(
                {
                    "_id": scan_id,
                    "pages": [
                        {
                            "xc": 0.5,
                            "yc": 0.5,
                            "width": 0.8,
                            "height": 0.8,
                        }
                    ],
                }
            )

        response = requests.patch(
            f"{API_URL}/{title_id}/update-pages",
            json=new_coordinates,
            headers=bearer_headers,
        )
        response.raise_for_status()
        print("Updated crop coordinates for all scans.")
        # Get coordinates
        response = requests.get(
            f"{API_URL}/integration/{title_id}/coordinates", headers=headers
        )
        response.raise_for_status()
        print(f"Coordinates for title {title_id}: {response.json()}")
        assert response.json()["pages"][0] == {
            "filename": "tests/sample_input/1-a0001.jpg",
            "xc": 0.5,
            "yc": 0.5,
            "width": 0.8,
            "height": 0.8,
            "angle": 0.0,
            "type": "single",
        }

        # Cleanup
        response = requests.post(
            f"{API_URL}/integration/{title_id}/complete", headers=headers
        )
        response.raise_for_status()

        assert response.json()["state"] == "retrain"
        files = os.listdir(f"{os.environ['SCANS_VOLUME_PATH']}/{title_id}")
        assert set(files) == {
            "1-a0001.jpg",
            "1-a0002.jpg",
            "1-a0003.jpg",
            "1-a0004.jpg",
            "1-a0005.jpg",
        }
        print("Marked title as completed")

        # At last, recreate the title and check if it will get replaced
        response = requests.post(
            f"{API_URL}/integration/create?group_id={group_id}",
            json={
                "filelist": [],
                "external_id": title_id,
            },
            headers=headers,
        )
        response.raise_for_status()
        sleep(5)
        response = requests.get(
            f"{API_URL}/integration/{title_id}/coordinates",
            headers=headers,
        )
        response.raise_for_status()
        response = response.json()
        assert len(response["pages"]) == 0
        print(
            "Successfully recreated title with same external_id, old scans were removed."
        )

    except Exception as e:
        print(f"An error occurred during testing: {e}")
        response = requests.delete(
            f"{API_URL}/groups/{group_id}", headers=bearer_headers
        )
        raise e

    # Delete the group
    response = requests.delete(f"{API_URL}/groups/{group_id}", headers=bearer_headers)
    response.raise_for_status()
    print("Cleaned up created group and title.")

    scan_directory = f"{os.environ['SCANS_VOLUME_PATH']}/{title_id}"
    assert not os.path.exists(scan_directory), (
        f"Scan directory {scan_directory} still exists after cleanup."
    )

    return True


assert main()
