"""``python -m ai_desktop.core``: run the headless core on stdin/stdout.

Shutdown: the shell sends ``shutdown`` and then closes stdin; the core also exits on plain EOF.
"""
import logging
import os
import sys

from ai_desktop import config
from ai_desktop.core import methods, protocol
from ai_desktop.core.server import make_stdio_server
from ai_desktop.settings_manager import SettingsManager
from ai_desktop.utils import storage
from ai_desktop.version import __version__


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Same boot order as the Qt app: schema first, then persisted settings over config defaults.
    storage.init_db()
    SettingsManager().load()
    if os.environ.get("AIDE_OLLAMA_URL"):  # test/dev override; never persisted
        config.OLLAMA_BASE_URL = os.environ["AIDE_OLLAMA_URL"].rstrip("/")
    handlers: dict = {}
    server = make_stdio_server(handlers)
    handlers.update(methods.build_handlers(server.emit))
    handlers["shutdown"] = lambda _params: (server.stop(), {})[1]
    server.emit("core.ready", {"version": __version__, "protocol": protocol.PROTOCOL_VERSION})
    server.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
