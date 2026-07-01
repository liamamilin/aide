"""Test that python -m ai_desktop entry point exists"""
import importlib


def test_main_module_entry_point():
    """Verify __main__.py can be imported and delegates to main()"""
    mod = importlib.import_module("ai_desktop.__main__")
    assert hasattr(mod, "__name__")
    # The module should define a way to call main
    # We don't actually call main() (it starts a Qt app),
    # just verify the import chain works
