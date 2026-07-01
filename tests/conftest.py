"""Shared pytest fixtures for UI tests.

Provides:
- qapp: session-scoped QApplication (required by pytest-qt)
- tmp_db: function-scoped temp SQLite database with monkey-patching
"""
import tempfile
import threading
from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

import ai_desktop.utils.storage as storage

_ORIG_DB_PATH = storage.DB_PATH


@pytest.fixture(scope="session")
def qapp():
    """Create a single QApplication for the entire test session.

    PyQt5 allows only one QApplication per process. pytest-qt provides
    its own qapp fixture, but we define ours to ensure consistency
    and to set QT_QPA_PLATFORM=offscreen for headless CI.
    """
    app = QApplication.instance()
    if app is None:
        # offscreen platform for headless CI
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([])
    yield app
    # Don't quit the app — other tests may still need it


@pytest.fixture()
def tmp_db():
    """Create a temporary SQLite database for each test function.

    Monkey-patches storage.DB_PATH to a temp file, initializes the DB,
    and restores the original path after the test.
    """
    storage._local = threading.local()  # fresh connection pool
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    storage.DB_PATH = Path(tmp.name)
    tmp.close()
    storage.init_db()
    yield storage.DB_PATH
    # Teardown: restore original path and clean up
    Path(storage.DB_PATH).unlink(missing_ok=True)
    storage.DB_PATH = _ORIG_DB_PATH
    storage._local = threading.local()


@pytest.fixture()
def fresh_db(tmp_db):
    """Alias for tmp_db — provides a fresh DB for each test."""
    return tmp_db
