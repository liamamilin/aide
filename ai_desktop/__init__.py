try:
    from importlib.metadata import version
    __version__ = version("ai-desktop-assistant")
except Exception:
    __version__ = "1.0.0"
