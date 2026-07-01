"""
集中式 QSS 样式 — 懒加载 + 显式颜色

所有样式在首次访问时生成（此时 QApplication 已创建），
使用 theme.ColorSet 的两套显式颜色，保证亮/暗模式均有足够对比度。
"""
from ai_desktop.ui.theme import current

_generated: dict[str, str] | None = None


def _generate() -> dict[str, str]:
    c = current()
    s = {}  # styles dict

    # ── 菜单 ──
    s["MENU"] = (
        f"QMenu {{ background: {c.window}; border: 1px solid {c.border}; "
        f"border-radius: 6px; padding: 4px 0; color: {c.text}; }}"
        f"QMenu::item {{ padding: 6px 24px; font-size: 13px; color: {c.text}; }}"
        f"QMenu::item:selected {{ background: {c.accent}; color: white; border-radius: 4px; }}"
        f"QMenu::separator {{ height: 1px; background: {c.border}; margin: 4px 10px; }}"
    )

    # ── 主按钮 ──
    s["BUTTON_PRIMARY"] = (
        f"QPushButton {{ background: {c.accent}; color: white; border: none; "
        f"border-radius: 5px; padding: 6px 14px; font-size: 12px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: {c.accent_hover}; }}"
        f"QPushButton:pressed {{ background: #0055b3; }}"
    )

    # ── 标题栏 ──
    s["TITLE_BAR"] = (
        f"background: {c.surface}; border: none; "
        f"border-top-left-radius: 10px; border-top-right-radius: 10px; color: {c.text};"
    )

    # ── 关闭按钮 ──
    s["CLOSE_BUTTON"] = (
        f"QPushButton {{ background: transparent; border: none; font-size: 16px; color: {c.text_secondary}; }}"
        f"QPushButton:hover {{ color: {c.text}; }}"
    )

    # ── 次要按钮 ──
    s["SECONDARY_BUTTON"] = (
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 5px;"
        f"padding: 2px 10px; font-size: 11px; color: {c.text}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
    )

    # ── 输入框 ──
    s["INPUT_FIELD"] = (
        f"QLineEdit {{ border: 1px solid {c.border}; border-radius: 6px; padding: 6px 10px;"
        f"font-size: 13px; background: {c.window}; color: {c.text}; }}"
        f"QLineEdit:focus {{ border-color: {c.accent}; }}"
    )

    # ── 下拉选择框 ──
    s["COMBO_BOX"] = (
        f"QComboBox {{ border: 1px solid {c.border}; border-radius: 5px; padding: 2px 6px;"
        f"  font-size: 11px; color: {c.text}; background: {c.window}; }}"
        f"QComboBox::drop-down {{ border: none; }}"
        f"QComboBox QAbstractItemView {{"
        f"  color: {c.text}; background: {c.window};"
        f"  selection-background-color: {c.accent};"
        f"  selection-color: white;"
        f"  outline: none;"
        f"}}"
    )

    # ── 滚动区域 ──
    s["SCROLL_AREA"] = (
        f"QScrollArea {{ border: none; background: {c.window}; }}"
        f"QScrollBar:vertical {{ width: 6px; }}"
        f"QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 3px; }}"
    )

    # ── 消息列表容器 ──
    s["MESSAGE_LIST"] = f"background: {c.window};"

    # ── 底部输入栏 ──
    s["INPUT_BAR"] = (
        f"background: {c.surface}; border: none; "
        f"border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;"
    )

    # ── 对话框基础样式 ──
    s["DIALOG_BASE"] = (
        f"QDialog {{ background: {c.window}; color: {c.text}; border: none; border-radius: 10px; }}"
    )

    # ── 删除按钮 ──
    s["DELETE_BUTTON"] = (
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 4px;"
        f"padding: 2px 8px; font-size: 11px; color: {c.error}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
    )

    # ── 表单输入框 ──
    s["FORM_WIDGET"] = (
        f"border: 1px solid {c.border}; border-radius: 4px; padding: 3px 6px;"
        f"background: {c.window}; color: {c.text};"
    )

    # ── Ollama 状态指示灯 ──
    s["OLLAMA_STATUS"] = f"background: {c.text_secondary}; border-radius: 5px; border: none;"
    s["OLLAMA_STATUS_OK"] = f"background: {c.success}; border-radius: 5px; border: none;"
    s["OLLAMA_STATUS_ERR"] = f"background: {c.error}; border-radius: 5px; border: none;"

    # ── 停止按钮 ──
    s["STOP_BUTTON"] = (
        f"QPushButton {{ background: {c.error}; color: white; border: none; border-radius: 5px;"
        f"padding: 6px 14px; font-size: 12px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: #c01010; }}"
    )

    # ── 齿轮/收起按钮 ──
    s["ICON_BUTTON"] = (
        f"QPushButton {{ background: transparent; border: none; font-size: 14px; color: {c.text_secondary}; }}"
        f"QPushButton:hover {{ color: {c.text}; background: {c.button}; border-radius: 12px; }}"
    )

    # ── 用户消息气泡 ──
    s["USER_BUBBLE"] = (
        f"QLabel {{ background: {c.accent}; color: white; border: none; border-radius: 10px;"
        f"padding: 8px 12px; font-size: 13px; }}"
    )

    # ── 助手消息气泡 ──
    s["ASSISTANT_BUBBLE"] = (
        f"QLabel {{ background: {c.button}; color: {c.text}; border: none; border-radius: 10px;"
        f"padding: 8px 12px; font-size: 13px; }}"
    )

    # ── 编辑按钮（用户消息 hover）──
    s["EDIT_BUTTON"] = (
        "QPushButton { background: transparent; border: none; font-size: 10px; color: rgba(255,255,255,0.7); }"
        "QPushButton:hover { color: white; background: rgba(255,255,255,0.15); border-radius: 3px; }"
    )

    # ── 复制按钮（助手消息 hover）──
    s["COPY_BUTTON"] = (
        f"QPushButton {{ background: transparent; border: none; font-size: 11px; color: {c.text_secondary}; }}"
        f"QPushButton:hover {{ color: {c.text}; background: {c.button}; border-radius: 3px; }}"
    )

    # ── 历史行 hover ──
    s["HISTORY_ROW"] = (
        f"QWidget {{ background: transparent; border: none; border-radius: 6px; }}"
        f"QWidget:hover {{ background: {c.button}; }}"
    )

    # ── 历史删除按钮 ──
    s["HISTORY_DELETE_BUTTON"] = (
        f"QPushButton {{ background: transparent; border: none; font-size: 12px; }}"
        f"QPushButton:hover {{ background: {c.button}; border-radius: 12px; }}"
    )

    # ── 搜索框 ──
    s["SEARCH_FIELD"] = (
        f"QLineEdit {{ border: none; border-bottom: 1px solid {c.border}; padding: 6px 12px;"
        f"font-size: 13px; background: {c.window}; color: {c.text}; }}"
        f"QLineEdit:focus {{ border-bottom-color: {c.accent}; }}"
    )

    # ── 设置滚动区域 ──
    s["SETTINGS_SCROLL"] = (
        f"QScrollArea {{ border: none; background: transparent; }}"
        f"QScrollBar:vertical {{ width: 6px; background: transparent; }}"
        f"QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 3px; min-height: 20px; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
    )

    # ── 取消按钮 ──
    s["CANCEL_BUTTON"] = (
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 4px;"
        f"padding: 6px 16px; color: {c.text}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
    )

    # ── 保存按钮 ──
    s["SAVE_BUTTON"] = (
        f"QPushButton {{ background: {c.accent}; color: white; border: none; border-radius: 4px;"
        f"padding: 6px 16px; }}"
        f"QPushButton:hover {{ background: {c.accent_hover}; }}"
    )

    # ── Emoji 选择按钮 ──
    s["EMOJI_PICK_BUTTON"] = (
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 4px;"
        f"font-size: 14px; color: {c.text}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
    )

    # ── Emoji 网格按钮 ──
    s["EMOJI_GRID_BUTTON"] = (
        f"QPushButton {{ font-size: 17px; border: 1px solid {c.border}; border-radius: 4px; background: {c.window}; }}"
        f"QPushButton:hover {{ background: {c.surface}; border-color: {c.accent}; }}"
    )

    # ── 对话窗口根样式 ──
    s["CHAT_DIALOG_ROOT"] = (
        f"QWidget {{ background: {c.window}; color: {c.text}; border: none; border-radius: 10px; }}"
    )

    # ── 标题栏图标/名称 ──
    s["TITLE_ICON"] = "font-size: 18px; background: none;"
    s["TITLE_NAME"] = f"font-weight: bold; font-size: 13px; background: none; color: {c.text};"

    # ── 模型下拉框（窄版）──
    s["MODEL_COMBO_BOX"] = (
        f"QComboBox {{ border: 1px solid {c.border}; border-radius: 5px; padding: 2px 4px;"
        f"  font-size: 11px; color: {c.text}; background: {c.window}; }}"
        f"QComboBox::drop-down {{ border: none; }}"
        f"QComboBox QAbstractItemView {{"
        f"  color: {c.text}; background: {c.window};"
        f"  selection-background-color: {c.accent};"
        f"  selection-color: white;"
        f"  outline: none;"
        f"  min-width: 120px;"
        f"}}"
    )

    # ── Agent 编辑按钮 ──
    s["AGENT_EDIT_BUTTON"] = (
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 4px;"
        f"padding: 2px 8px; font-size: 11px; color: {c.text}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
    )

    # ── 表单输入框（带 focus 高亮）──
    s["FORM_INPUT"] = (
        f"QLineEdit {{ border: 1px solid {c.border}; border-radius: 4px;"
        f"padding: 4px 8px; color: {c.text}; background: {c.window}; }}"
        f"QLineEdit:focus {{ border-color: {c.accent}; }}"
    )

    # ── 表单多行输入 ──
    s["FORM_TEXTAREA"] = (
        f"QPlainTextEdit {{ border: 1px solid {c.border}; border-radius: 4px; padding: 6px 8px;"
        f"font-size: 12px; font-family: Menlo, monospace; color: {c.text}; background: {c.window}; }}"
        f"QPlainTextEdit:focus {{ border-color: {c.accent}; }}"
    )

    # ── 标签文字 ──
    s["LABEL"] = f"color: {c.text}; background: none;"
    s["LABEL_SECONDARY"] = f"font-size: 11px; color: {c.text_secondary}; background: none;"
    s["LABEL_BOLD"] = f"font-weight: bold; font-size: 13px; background: none; color: {c.text};"

    # ── 新增 Agent 按钮 ──
    s["ADD_AGENT_BUTTON"] = (
        f"QPushButton {{ background: {c.accent}; color: white; border: none; border-radius: 5px;"
        f"padding: 2px 14px; font-size: 12px; }}"
        f"QPushButton:hover {{ background: {c.accent_hover}; }}"
    )

    # ── Agent 列表底部栏 ──
    s["AGENT_LIST_BAR"] = (
        f"background: {c.surface}; border: none; "
        f"border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;"
    )

    # ── 透明背景 ──
    s["TRANSPARENT"] = "background: transparent;"

    # ── 多行输入框 ──
    s["INPUT_AREA"] = (
        f"QPlainTextEdit {{ border: 1px solid {c.border}; border-radius: 8px;"
        f"padding: 8px 10px; font-size: 13px; background: {c.window}; color: {c.text}; }}"
        f"QPlainTextEdit:focus {{ border-color: {c.accent}; }}"
    )

    # ── 工具栏（标题栏下方）──
    s["TOOLBAR"] = (
        f"background: {c.surface}; border: none; "
        f"border-bottom: 1px solid {c.border};"
    )

    # ── 代码块复制链接 ──
    s["CODE_LINK"] = f"color: {c.text_secondary}; text-decoration: none; font-size: 11px;"
    s["CODE_LINK_HOVER"] = f"color: {c.text}; text-decoration: underline; font-size: 11px;"

    return s


def __getattr__(name: str) -> str:
    global _generated
    if _generated is None:
        _generated = _generate()
    if name not in _generated:
        raise AttributeError(f"styles has no attribute {name!r}")
    return _generated[name]
