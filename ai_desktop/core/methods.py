"""Method handlers exposed to the shell. P0a: liveness and identity only."""
from __future__ import annotations

import os
import platform
import sys
from typing import Any

from ai_desktop.core import protocol
from ai_desktop.version import __version__


def ping(_params: dict[str, Any]) -> dict[str, Any]:
    return {"pong": True}


def health(_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": __version__,
        "protocol": protocol.PROTOCOL_VERSION,
        "python": platform.python_version(),
        # Packaging invariant: the shipped core must never pull in Qt (GPL, lifecycle bugs).
        "qt_loaded": any(name == "PyQt5" or name.startswith("PyQt5.") for name in sys.modules),
        "data_dir": os.environ.get("AIDE_DATA_DIR", ""),
    }
