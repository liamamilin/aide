"""
简易 Markdown → HTML 渲染

特性：
  - 颜色参数化（支持亮/暗两套，见 theme.py）
  - 代码块自动生成 copy:// URL（供 QTextBrowser anchorClicked 拦截）
"""
import html
import re
from typing import Optional

from ai_desktop.ui.theme import MarkdownColors, current_markdown


def to_html(text: str, colors: Optional[MarkdownColors] = None) -> tuple[str, dict[str, str]]:
    renderer = _Renderer(text, colors or current_markdown())
    html_out = renderer.render()
    return html_out, renderer.code_map


class _Renderer:
    def __init__(self, text: str, colors: MarkdownColors):
        self.lines = text.split("\n")
        self.c = colors
        self.result: list[str] = []
        self.para_buf: list[str] = []
        self.in_code_block = False
        self.code_lines: list[str] = []
        self.code_idx = 0
        self.code_map: dict[str, str] = {}

    def render(self) -> str:
        for line in self.lines:
            if line.strip().startswith("```"):
                if not self.in_code_block:
                    self._flush_para()
                    self.in_code_block = True
                else:
                    self._flush_code()
                    self.in_code_block = False
                continue

            if self.in_code_block:
                self.code_lines.append(line)
                continue

            if not line.strip():
                self._flush_para()
                self.result.append("<br>")
                continue

            if _is_single(line):
                self._flush_para()
                self.result.append(_render_single(line, self.c))
                continue

            self.para_buf.append(line)

        self._flush_para()
        self._flush_code()
        return "".join(self.result)

    def _flush_para(self) -> None:
        if not self.para_buf:
            return
        text = " ".join(self.para_buf)
        self.para_buf = []
        if text.strip():
            self.result.append(
                f'<p style="margin:4px 0;line-height:1.6;">{_fmt(text, self.c)}</p>'
            )

    def _flush_code(self) -> None:
        if not self.code_lines:
            return
        code = "\n".join(self.code_lines)
        escaped = html.escape(code)
        url = f"copy://codeblock_{self.code_idx}"
        self.code_map[url] = code
        self.result.append(
            f'<div style="margin:8px 0;background:{self.c.pre_bg};">'
            f'<a href="{url}" style="color:{self.c.pre_text};font-size:11px;'
            f'text-decoration:none;">复制代码</a>'
            f'<br>'
            f'<pre style="background:{self.c.pre_bg};color:{self.c.pre_text};'
            f'padding:6px 12px 10px;font-size:12px;line-height:1.5;margin:0;">{escaped}</pre>'
            f'</div>'
        )
        self.code_lines = []
        self.code_idx += 1


# ── 单行结构 ──

_SINGLE_PATTERNS = (r"^#{1,3}\s", r"^[-*]\s", r"^\d+\.\s", r"^[-*_]{3,}$")


def _is_single(line: str) -> bool:
    s = line.strip()
    return any(re.match(p, s) for p in _SINGLE_PATTERNS)


def _render_single(line: str, c: MarkdownColors) -> str:
    s = line.strip()

    m = re.match(r"^(#{1,3})\s+(.+)$", s)
    if m:
        level = len(m.group(1))
        content = _fmt(m.group(2), c)
        sizes = {1: 18, 2: 15, 3: 14}
        margins = {1: "12px 0 6px", 2: "10px 0 4px", 3: "8px 0 2px"}
        return (
            f'<h{level} style="font-size:{sizes[level]}px;'
            f'font-weight:bold;margin:{margins[level]};'
            f'color:{c.heading};">{content}</h{level}>'
        )

    if re.match(r"^[-*]\s", s):
        content = _fmt(re.sub(r"^[-*]\s+", "", s), c)
        return (
            f'<div style="margin:2px 0 2px 16px;">'
            f'<span style="color:{c.bullet};">•</span> {content}</div>'
        )

    m = re.match(r"^(\d+)\.\s+(.+)$", s)
    if m:
        content = _fmt(m.group(2), c)
        return (
            f'<div style="margin:2px 0 2px 16px;">'
            f'<span style="color:{c.bullet};">{m.group(1)}.</span> {content}</div>'
        )

    if re.match(r"^[-*_]{3,}$", s):
        return f'<hr style="border:none;border-top:1px solid {c.hr};margin:8px 0;">'

    return f'<p style="margin:4px 0;line-height:1.6;">{_fmt(s, c)}</p>'


# ── 行内格式 ──

def _fmt(text: str, c: MarkdownColors) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(
        r"`([^`]+)`",
        f'<code style="background:{c.inline_code_bg};color:{c.inline_code_text};'
        f'padding:1px 5px;border-radius:3px;font-family:Menlo,monospace;font-size:12px;">\\1</code>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    return text
