# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the headless Aide core (the Python helper inside the native Aide.app).

Qt is excluded on purpose: the core must stay GUI-free (GPL boundary, no event-loop lifecycle bugs).
"""
import os
import runpy

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), ".."))
VERSION = runpy.run_path(os.path.join(ROOT, "ai_desktop", "version.py"))["__version__"]

a = Analysis(
    [os.path.join(ROOT, "ai_desktop", "core", "__main__.py")],
    pathex=[ROOT],
    datas=[],
    # Imported lazily at runtime (EventKit for Reminders) or only inside functions.
    hiddenimports=["EventKit", "objc", "Foundation", "AppKit"],
    excludes=["PyQt5", "qfluentwidgets", "qframelesswindow", "pynput", "tkinter", "spacy", "kokoro", "misaki",
              "torch", "numpy", "PIL", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="aide-core", console=True, debug=False,
          strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="aide-core")
