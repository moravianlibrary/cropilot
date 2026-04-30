import os
import time
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
    token = response.json()["access_token"]
    return token

def create_group():
    response = requests.post(
            f"{BASE_URL}/groups",
            json={
                "name": "Test titles API group",
                "description": "Group for integration testing",
            },
            headers=headers(),
        )
    response.raise_for_status()
    group_id = response.json()["id"]
    print(f"Created group with ID: {group_id}")
    return group_id

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


def tiny_png_bytes():
    # 1x1 transparent PNG
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
        b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

TOKEN = login_for_token()
GROUP_ID = create_group()

def test_books_endpoints_flow():
    external_id = f"pytest-{uuid.uuid4()}"

    # POST /create
    create_resp = requests.post(
        f"{BASE_URL}/create",
        params={"group_id": GROUP_ID},
        json={
            "external_id": external_id,
            "metadata": {"source": "pytest"},
        },
        headers=headers(),
        timeout=30,
    )
    title = assert_ok(create_resp)
    title_id = get_id(title, "title_id", "_id", "id")
    print(f"Created title with ID: {title_id}")

    try:
        # POST /{title_id}/upload-scan
        upload_resp = requests.post(
            f"{BASE_URL}/{title_id}/upload-scan",
            files={
                "scan_data": (
                    "test_scan.png",
                    tiny_png_bytes(),
                    "image/png",
                )
            },
            headers=headers(),
            timeout=30,
        )
        upload_data = assert_ok(upload_resp)
        print(f"Uploaded scan for title ID: {title_id}")

        # POST /{title_id}/process
        process_resp = requests.post(
            f"{BASE_URL}/{title_id}/process",
            headers=headers(),
            timeout=30,
        )
        assert_ok(process_resp)
        print(f"Processed title ID: {title_id}")
        time.sleep(10)

        # GET /{title_id}/status
        status_resp = requests.get(
            f"{BASE_URL}/{title_id}/status",
            headers=headers(),
            timeout=30,
        )
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.text == '"ready"'
        print(f"Title ID: {title_id} is ready")

        # GET /{title_id}/scans
        scans_resp = requests.get(
            f"{BASE_URL}/{title_id}/scans",
            headers=headers(),
            timeout=30,
        )
        scans = assert_ok(scans_resp)
        scans = scans.get("scans")
        print(f"Retrieved scans {scans}")

        assert isinstance(scans, list)
        scan_id = None
        if scans:
            scan_id = scans[0].get("_id") or scans[0].get("id") or scans[0].get("scan_id")

        # fallback from upload response if API returns scan id there
        if not scan_id and isinstance(upload_data, dict):
            scan_id = upload_data.get("scan_id") or upload_data.get("_id") or upload_data.get("id")

        assert scan_id, f"Could not determine scan_id from scans={scans}, upload={upload_data}"

        # GET /{title_id}/scans?scan_id=...
        scan_by_id_resp = requests.get(
            f"{BASE_URL}/{title_id}/scans",
            params={"scan_id": scan_id},
            headers=headers(),
            timeout=30,
        )
        assert_ok(scan_by_id_resp)
        print(f"Retrieved scan by ID {scan_id} for title ID: {title_id}")

        # GET /{title_id}/predicted-scans
        predicted_resp = requests.get(
            f"{BASE_URL}/{title_id}/predicted-scans",
            headers=headers(),
            timeout=30,
        )
        predicted = assert_ok(predicted_resp)
        print(f"Retrieved predicted scans {predicted}")

        # GET /{title_id}/files?scan_id=...
        file_resp = requests.get(
            f"{BASE_URL}/{title_id}/files",
            params={"scan_id": scan_id},
            headers=headers(),
            timeout=30,
        )
        assert file_resp.status_code == 200, file_resp.text
        assert file_resp.content

        # GET /{title_id}/thumbnails?scan_id=...
        thumb_resp = requests.get(
            f"{BASE_URL}/{title_id}/thumbnails",
            params={"scan_id": scan_id},
            headers=headers(),
            timeout=30,
        )
        assert thumb_resp.status_code == 200, thumb_resp.text
        assert thumb_resp.content
        print(f"Retrieved file and thumbnail for scan ID: {scan_id}")

        # PATCH /{title_id}/update-pages
        update_pages_resp = requests.patch(
            f"{BASE_URL}/{title_id}/update-pages",
            json=[
                {
                    "_id": scan_id,
                    "pages": [
                        {
                            "xc": 100,
                            "yc": 100,
                            "width": 50,
                            "height": 80,
                            "confidence": 1,
                            "angle": 0,
                            "flags": [],
                        }
                    ],
                }
            ],
            headers=headers(),
            timeout=30,
        )
        assert_ok(update_pages_resp)
        print(f"Updated pages for scan ID: {scan_id}")

        # PATCH /{title_id}
        update_resp = requests.patch(
            f"{BASE_URL}/{title_id}",
            json={
                "external_id": external_id + "-updated"
            },
            headers=headers(),
            timeout=30,
        )
        assert_ok(update_resp)
        print(f"Updated title ID: {title_id} with new external_id")

        # PATCH /{title_id}/reset
        reset_resp = requests.patch(
            f"{BASE_URL}/{title_id}/reset",
            headers=headers(),
            timeout=30,
        )
        assert_ok(reset_resp)
        print(f"Reset title ID: {title_id}")

    finally:
        # DELETE /{title_id}
        delete_resp = requests.delete(
            f"{BASE_URL}/{title_id}",
            headers=headers(),
            timeout=30,
        )
        assert delete_resp.status_code == 200, delete_resp.text
        print(f"Deleted title ID: {title_id}")

        # Delete the group
        response = requests.delete(f"{BASE_URL}/groups/{GROUP_ID}", headers=headers())
        print(f"Deleted group ID: {GROUP_ID}")
        response.raise_for_status()

        scan_directory = f"{os.environ['SCANS_VOLUME_PATH']}/{title_id}"
        assert not os.path.exists(scan_directory), f"Scan directory {scan_directory} still exists after cleanup."
    
    return True


assert test_books_endpoints_flow()