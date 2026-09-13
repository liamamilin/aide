"""Support for the ``aide`` and ``python -m ai_desktop`` entry points."""

from __future__ import annotations

import sys

from ai_desktop import __version__


def main() -> None:
    if "--version" in sys.argv[1:]:
        print(__version__)
        return

    from ai_desktop.main import main as run_app

    run_app()


if __name__ == "__main__":
    main()
