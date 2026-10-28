"""Headless Aide core: the Python side of the native shell (no Qt, no UI).

The macOS shell spawns ``python -m ai_desktop.core`` and talks to it over
stdin/stdout using newline-delimited JSON (see ``protocol.py``).
"""
