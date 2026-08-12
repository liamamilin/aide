"""Support for `python -m ai_desktop` entry point"""
from multiprocessing import freeze_support

from ai_desktop.main import main

if __name__ == "__main__":
    freeze_support()
    main()
