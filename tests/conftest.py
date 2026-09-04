"""Shared test fixtures. No network is used by non-integration tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kitchen_prep import config  # noqa: E402
from kitchen_prep.data_access.store import LocalJsonStore  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def ensure_sales_history() -> None:
    """Generate the synthetic sales history once if it is missing."""
    if not config.SALES_HISTORY_PATH.exists():
        from scripts.generate_sales_history import main as gen

        gen()


@pytest.fixture()
def tmp_store(tmp_path) -> LocalJsonStore:
    return LocalJsonStore(base_dir=tmp_path / "out")


DEMO_DATE = config.DEMO_DATE
