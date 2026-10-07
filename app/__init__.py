# Keep in sync with pyproject.toml (tests/test_version.py checks it). The image
# must not get a newer pyproject.toml than the base image: `uv run` would try
# to re-lock against it and the API would not start.
__version__ = "1.2.7"
