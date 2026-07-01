"""Smoke tests for AI Desktop Assistant"""
from ai_desktop.capture.text_normalizer import normalize
from ai_desktop.ui.markdown import to_html


def test_normalize():
    assert normalize("  a  \n\n\n  b  ") == "a  \n\n  b"
    assert normalize("") == ""
    assert normalize("hello") == "hello"


def test_markdown():
    html, _ = to_html("**bold** and some `code`")
    assert "<b>bold</b>" in html
    assert "<code" in html

    html2, _ = to_html("```\nhello\nworld\n```")
    assert "<pre" in html2
    assert "hello" in html2

    safe, _ = to_html("print({1: 2})")
    assert "1: 2" in safe

    escaped, _ = to_html("<script>alert(1)</script> & text")
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; text" in escaped
    assert "<script>" not in escaped


def test_markdown_heading():
    html, _ = to_html("## Title\n\nbody")
    assert "<h2" in html
    assert "Title" in html
    assert "body" in html


def test_markdown_list():
    html, _ = to_html("- item1\n- item2")
    assert "item1" in html
    assert "item2" in html


def test_markdown_code_map():
    html, cm = to_html("```\ncode\n```")
    assert "copy://codeblock_0" in html
    assert "copy://codeblock_0" in cm
    assert cm["copy://codeblock_0"] == "code"


if __name__ == "__main__":
    test_normalize()
    test_markdown()
    test_markdown_heading()
    test_markdown_list()
    test_markdown_code_map()
    print("All smoke tests passed!")
