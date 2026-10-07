import tomllib
from pathlib import Path

from app import __version__


def test_app_version_matches_pyproject():
    with open(Path(__file__).parent.parent / "pyproject.toml", "rb") as f:
        assert __version__ == tomllib.load(f)["project"]["version"]
