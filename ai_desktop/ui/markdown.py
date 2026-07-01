"""
简易 Markdown → HTML 渲染

仅处理 LLM 响应中最常见的格式：
- 代码块 (``` ... ```)
- 行内代码 (`...`)
- 标题 (# ## ###)
- 粗体 / 斜体
- 无序 / 有序列表
- 分隔线
"""
import html
import re


def to_html(text: str) -> str:
    return _Renderer(text).render()


class _Renderer:
    def __init__(self, text: str):
        self.lines = text.split("\n")
        self.result: list[str] = []
        self.para_buf: list[str] = []
        self.in_code_block = False
        self.code_lines: list[str] = []

    def render(self) -> str:
        for line in self.lines:
            # ── 代码块边界 ──
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

            # ── 空行 → 段落分隔 ──
            if not line.strip():
                self._flush_para()
                self.result.append("<br>")
                continue

            # ── 单行结构：标题 / 列表 / 分隔线 ──
            if _is_single(line):
                self._flush_para()
                self.result.append(_render_single(line))
                continue

            # ── 普通文本 → 累积为段落 ──
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
            self.result.append(f'<p style="margin:4px 0;line-height:1.6;">{_fmt(text)}</p>')

    def _flush_code(self) -> None:
        if not self.code_lines:
            return
        code = "\n".join(self.code_lines)
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.result.append(
            '<pre style="background:#1e1e1e;color:#d4d4d4;'
            'padding:12px 14px;border-radius:6px;'
            'font-size:12px;line-height:1.5;overflow-x:auto;'
            f'margin:8px 0;">{escaped}</pre>'
        )
        self.code_lines = []


# ── 单行结构 ──────────────────────────────────────────

def _is_single(line: str) -> bool:
    s = line.strip()
    return bool(
        re.match(r"^#{1,3}\s", s)
        or re.match(r"^[-*]\s", s)
        or re.match(r"^\d+\.\s", s)
        or re.match(r"^[-*_]{3,}$", s)
    )


def _render_single(line: str) -> str:
    s = line.strip()

    # 标题
    m = re.match(r"^(#{1,3})\s+(.+)$", s)
    if m:
        level = len(m.group(1))
        content = _fmt(m.group(2))
        sizes = {1: 18, 2: 15, 3: 14}
        margins = {1: "12px 0 6px", 2: "10px 0 4px", 3: "8px 0 2px"}
        return (
            f'<h{level} style="font-size:{sizes[level]}px;'
            f'font-weight:bold;margin:{margins[level]};'
            f'color:#1a1a1a;">{content}</h{level}>'
        )

    # 无序列表
    if re.match(r"^[-*]\s", s):
        content = _fmt(re.sub(r"^[-*]\s+", "", s))
        return (
            '<div style="margin:2px 0 2px 16px;">'
            f'<span style="color:#555;">•</span> {content}</div>'
        )

    # 有序列表
    m = re.match(r"^(\d+)\.\s+(.+)$", s)
    if m:
        content = _fmt(m.group(2))
        return (
            '<div style="margin:2px 0 2px 16px;">'
            f'<span style="color:#555;">{m.group(1)}.</span> {content}</div>'
        )

    # 分隔线
    if re.match(r"^[-*_]{3,}$", s):
        return '<hr style="border:none;border-top:1px solid #e0e0e0;margin:8px 0;">'

    return f'<p style="margin:4px 0;line-height:1.6;">{_fmt(s)}</p>'


# ── 行内格式 ──────────────────────────────────────────

def _fmt(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#f0f0f0;padding:1px 5px;'
        r'border-radius:3px;font-family:Menlo,monospace;font-size:12px;">\1</code>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    return text
