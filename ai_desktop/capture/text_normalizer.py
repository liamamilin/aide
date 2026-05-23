"""
文本规范化模块
"""
import re
from ai_desktop import config


def normalize(text: str) -> str:
    """
    清洗用户选中的文本：

    - 去除首尾空白
    - 压缩连续空行（最多保留一行）
    - 截断超长文本
    """
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)

    if len(text) > config.MAX_TEXT_LENGTH:
        text = text[: config.MAX_TEXT_LENGTH]
        text += "\n\n…（文本过长，已截断）"

    return text
