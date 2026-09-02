"""Test bootstrap.

``app.deps`` instantiates settings from environment variables at import time,
so importing any route module needs these set. The values are placeholders:
unit tests use duck-typed fakes and never connect to a database.
"""

import os

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB", "cropilot_test")
os.environ.setdefault("PWD_SECRET", "test-secret")
os.environ.setdefault("SCANS_VOLUME_PATH", "/tmp/cropilot-test-scans")
os.environ.setdefault("MODELS_VOLUME_PATH", "/tmp/cropilot-test-models")
