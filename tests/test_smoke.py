"""Smoke tests for AI Desktop Assistant"""
from ai_desktop.capture.text_normalizer import normalize
from ai_desktop.ui.markdown import to_html


def test_normalize():
    assert normalize("  a  \n\n\n  b  ") == "a  \n\n  b"
    assert normalize("") == ""
    assert normalize("hello") == "hello"


def test_markdown():
    html = to_html("**bold** and some `code`")
    assert "<b>bold</b>" in html
    assert "<code" in html

    html2 = to_html("```\nhello\nworld\n```")
    assert "<pre" in html2
    assert "hello" in html2

    safe = to_html("print({1: 2})")
    assert "1: 2" in safe


def test_markdown_heading():
    html = to_html("## Title\n\nbody")
    assert "<h2" in html
    assert "Title" in html
    assert "body" in html


def test_markdown_list():
    html = to_html("- item1\n- item2")
    assert "item1" in html
    assert "item2" in html


if __name__ == "__main__":
    test_normalize()
    test_markdown()
    test_markdown_heading()
    test_markdown_list()
    print("All smoke tests passed!")
