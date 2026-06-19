"""Shared pytest fixtures for the graph test suite."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wikimcp.wiki import graph

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "wiki_realistic"


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    """Isolate the module-level graph cache between tests."""
    graph.invalidate_cache()
    yield
    graph.invalidate_cache()


@pytest.fixture
def realistic_wiki() -> Path:
    """Path to the read-only realistic fixture wiki (24 pages, known truth)."""
    return FIXTURE_ROOT


@pytest.fixture
def realistic_wiki_copy(tmp_path: Path) -> Path:
    """A writable copy of the realistic fixture wiki for mutation tests."""
    dest = tmp_path / "wiki_realistic"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest
