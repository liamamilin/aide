"""``python -m ai_desktop.core``: run the headless core on stdin/stdout.

Shutdown: the shell sends ``shutdown`` and then closes stdin; the core also exits on plain EOF.
"""
import logging
import sys

from ai_desktop.core import methods, protocol
from ai_desktop.core.server import make_stdio_server
from ai_desktop.version import __version__


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    handlers = {"ping": methods.ping, "health": methods.health}
    server = make_stdio_server(handlers)
    server.emit("core.ready", {"version": __version__, "protocol": protocol.PROTOCOL_VERSION})
    handlers["shutdown"] = lambda _params: (server.stop(), {})[1]
    server.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
