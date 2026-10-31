"""Fixtures shared by the test modules."""

from pathlib import Path

import pytest

from emporio_agent.data_loader import build_database
from emporio_agent.policies import load_policy_text
from tests.helpers import DATA_DIR


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """SQLite database built once from the real store data."""
    path = tmp_path_factory.mktemp("db") / "emporio.db"
    build_database(DATA_DIR, path)
    return path


@pytest.fixture(scope="session")
def policy() -> str:
    """Clean text of the real policy manual."""
    return load_policy_text(DATA_DIR / "politicas_da_loja.pdf")
