from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def component_root(repo_root: Path) -> Path:
    return repo_root / "custom_components" / "intersvyaz"
