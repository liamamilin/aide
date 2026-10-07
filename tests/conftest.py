"""Shared pytest fixtures for UI tests.

Provides:
- qapp: session-scoped QApplication (required by pytest-qt)
- tmp_db: function-scoped temp SQLite database with monkey-patching
- _isolated_session_data: session-scoped temporary data root for every test
"""
import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Tests must never reach the user's real database or attachments. This must be
# set before storage is imported, because DB_PATH is resolved exactly once at
# import time. Assign rather than setdefault: an AIDE_DATA_DIR already present
# in the developer's shell must not redirect the suite back to real user data.
# Both storage._resolve_db_path and images._app_support_dir honour this
# variable, so init_db()'s attachment collection also stays inside the
# temporary root instead of scanning the real attachments directory.
_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="aide-tests-")
os.environ["AIDE_DATA_DIR"] = _SESSION_DATA_DIR

import ai_desktop.utils.storage as storage  # noqa: E402 - DB_PATH resolves once at import

_ORIG_DB_PATH = storage.DB_PATH


@pytest.fixture(scope="session", autouse=True)
def _isolated_session_data():
    """Give every test an initialised schema without touching user data.

    Without this, a test that reads settings without requesting tmp_db only
    passes where a real database happens to exist. On a fresh CI runner
    _resolve_db_path returns a path that does not exist yet, _conn creates an
    empty file, and the first settings read fails with "no such table".
    """
    # resolve() on both sides: mkdtemp returns /var/folders/... which is a
    # symlink to /private/var/folders/... on macOS.
    try:
        assert Path(storage.DB_PATH).resolve().parent == Path(_SESSION_DATA_DIR).resolve(), \
            "测试数据库必须位于会话临时目录内"
        storage.init_db()
        yield
    finally:
        # try/finally, not a plain post-yield block: if the guard above fails the
        # generator never yields, and the temporary root would be left behind.
        storage.close_db()
        shutil.rmtree(_SESSION_DATA_DIR, ignore_errors=True)


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


@pytest.fixture
def ollama_server(monkeypatch):
    from ai_desktop import config
    from tests.fake_ollama import FakeOllama
    server = FakeOllama()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", server.url)
    yield server
    server.close()
