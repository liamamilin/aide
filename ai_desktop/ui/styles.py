"""
集中式 QSS 样式 — 懒加载 + 显式颜色

所有样式在首次访问时生成（此时 QApplication 已创建），
使用 theme.ColorSet 的两套显式颜色，保证亮/暗模式均有足够对比度。
"""
from ai_desktop.ui.theme import SYSTEM_FONT, TOKENS, current

_generated: dict[str, str] | None = None


def _generate() -> dict[str, str]:
    c = current()
    s = {}
    disabled = f"background: {c.disabled}; color: {c.disabled_text}; border-color: {c.border};"

    # ── 菜单 ──
    s["MENU"] = (
        f"QMenu {{ background: {c.surface_elevated}; border: 1px solid {c.border}; "
        f"border-radius: 8px; padding: 5px; color: {c.text}; font-family: {SYSTEM_FONT}; }}"
        f"QMenu::item {{ padding: 6px 22px 6px 10px; font-size: 13px; color: {c.text}; border-radius: 5px; }}"
        f"QMenu::item:selected {{ background: {c.accent}; color: white; border-radius: 4px; }}"
        f"QMenu::separator {{ height: 1px; background: {c.separator}; margin: 4px 8px; }}"
    )

    # ── 主按钮 ──
    s["BUTTON_PRIMARY"] = (
        f"QPushButton {{ background: {c.accent}; color: white; border: none; "
        f"border-radius: 7px; padding: 5px 14px; min-height: 18px; font-size: 12px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {c.accent_hover}; }}"
        f"QPushButton:pressed {{ background: {c.accent_hover}; }}"
        f"QPushButton:disabled {{ {disabled} }}"
    )

    # ── 标题栏 ──
    s["TITLE_BAR"] = (
        f"background: {c.surface_elevated}; border: none; border-bottom: 1px solid {c.separator}; "
        f"border-top-left-radius: 12px; border-top-right-radius: 12px; color: {c.text};"
    )

    # ── 关闭按钮 ──
    s["CLOSE_BUTTON"] = (
        f"QPushButton {{ background: transparent; border: none; font-size: 16px; color: {c.text_secondary}; }}"
        f"QPushButton:hover {{ color: {c.text}; }}"
    )

    # ── 次要按钮 ──
    s["SECONDARY_BUTTON"] = (
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 7px;"
        f"padding: 3px 10px; font-size: 11px; color: {c.text}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
        f"QPushButton:disabled {{ {disabled} }}"
    )

    # ── 输入框 ──
    s["INPUT_FIELD"] = (
        f"QLineEdit {{ border: 1px solid {c.border}; border-radius: 7px; padding: 5px 9px;"
        f"font-size: 13px; background: {c.surface_elevated}; color: {c.text}; }}"
        f"QLineEdit:focus {{ border: 2px solid {c.focus_ring}; padding: 4px 8px; }}"
        f"QLineEdit:disabled {{ {disabled} }}"
    )

    # ── 下拉选择框 ──
    s["COMBO_BOX"] = (
        f"QComboBox {{ border: 1px solid {c.border}; border-radius: 7px; padding: 3px 7px;"
        f"  font-size: 11px; color: {c.text}; background: {c.button}; }}"
        f"QComboBox::drop-down {{ border: none; }}"
        f"QComboBox QAbstractItemView {{"
        f"  color: {c.text}; background: {c.surface_elevated};"
        f"  selection-background-color: {c.accent};"
        f"  selection-color: white;"
        f"  outline: none;"
        f"}}"
        f"QComboBox:focus {{ border: 2px solid {c.focus_ring}; }}"
        f"QComboBox:disabled {{ {disabled} }}"
    )

    # ── 滚动区域 ──
    s["SCROLL_AREA"] = (
        f"QScrollArea {{ border: none; background: {c.window}; }}"
        f"QScrollBar:vertical {{ width: 7px; background: transparent; margin: 3px 1px; }}"
        f"QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 3px; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    )

    # ── 消息列表容器 ──
    s["MESSAGE_LIST"] = f"background: {c.window};"

    # ── 底部输入栏 ──
    s["INPUT_BAR"] = (
        f"background: {c.surface_elevated}; border: none; border-top: 1px solid {c.separator}; "
        f"border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;"
    )

    # ── 对话框基础样式 ──
    s["DIALOG_BASE"] = (
        f"QDialog {{ background: {c.window}; color: {c.text}; border: 1px solid {c.border}; "
        f"border-radius: 12px; font-family: {SYSTEM_FONT}; font-size: 13px; }}"
    )

    # ── 删除按钮 ──
    s["DELETE_BUTTON"] = (
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 4px;"
        f"padding: 2px 8px; font-size: 11px; color: {c.error}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
    )

    # ── 表单输入框 ──
    s["FORM_WIDGET"] = (
        f"border: 1px solid {c.border}; border-radius: 7px; padding: 5px 8px; min-height: 18px;"
        f"background: {c.surface_elevated}; color: {c.text}; selection-background-color: {c.accent};"
    )
    s["FORM_SPIN"] = (
        f"QSpinBox, QDoubleSpinBox {{ border: 1px solid {c.border}; border-radius: 8px; padding: 6px 9px; "
        f"min-height: 20px; background: {c.surface_elevated}; color: {c.text}; "
        f"selection-background-color: {c.accent}; }}"
        f"QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {c.focus_ring}; padding: 5px 8px; }}"
        "QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, "
        "QDoubleSpinBox::down-button { width: 0px; height: 0px; border: none; }"
    )

    # ── Ollama 状态指示灯 ──
    s["OLLAMA_STATUS"] = f"background: {c.text_secondary}; border-radius: 5px; border: none;"
    s["OLLAMA_STATUS_OK"] = f"background: {c.success}; border-radius: 5px; border: none;"
    s["OLLAMA_STATUS_ERR"] = f"background: {c.error}; border-radius: 5px; border: none;"

    # ── 停止按钮 ──
    s["STOP_BUTTON"] = (
        f"QPushButton {{ background: {c.error}; color: white; border: none; border-radius: 7px;"
        f"padding: 6px 14px; font-size: 12px; font-weight: bold; }}"
        f"QPushButton:hover {{ background: {c.error}; }}"
    )

    # ── 齿轮/收起按钮 ──
    s["ICON_BUTTON"] = (
        f"QPushButton {{ background: transparent; border: none; font-size: 14px; color: {c.text_secondary}; }}"
        f"QPushButton:hover {{ color: {c.text}; background: {c.button_hover}; border-radius: 7px; }}"
        "QPushButton::menu-indicator { image: none; width: 0px; }"
    )

    # ── 用户消息气泡 ──
    s["USER_BUBBLE"] = (
        f"QLabel {{ background: {c.user_bubble}; color: white; border: none; border-radius: 11px;"
        f"padding: 8px 12px; font-size: 13px; }}"
    )

    # ── 助手消息气泡 ──
    s["ASSISTANT_BUBBLE"] = (
        f"QLabel {{ background: {c.surface_elevated}; color: {c.text}; "
        f"border: 1px solid {c.separator}; border-radius: 11px;"
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
        f"QWidget#listRow {{ background: {c.surface_elevated}; border: 1px solid {c.separator}; "
        f"border-radius: 9px; }}"
        f"QWidget#listRow:hover {{ background: {c.button_hover}; border-color: {c.border}; }}"
    )

    # ── 历史删除按钮 ──
    s["HISTORY_DELETE_BUTTON"] = (
        f"QPushButton {{ background: transparent; border: none; font-size: 12px; }}"
        f"QPushButton:hover {{ background: {c.button}; border-radius: 12px; }}"
    )

    # ── 搜索框 ──
    s["SEARCH_FIELD"] = (
        f"QLineEdit {{ border: 1px solid {c.border}; border-radius: 8px; margin: 10px 12px; padding: 6px 10px;"
        f"font-size: 13px; background: {c.surface_elevated}; color: {c.text}; }}"
        f"QLineEdit:focus {{ border: 2px solid {c.focus_ring}; padding: 5px 9px; }}"
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
        f"QPushButton {{ background: {c.button}; border: 1px solid {c.border}; border-radius: 7px;"
        f"padding: 6px 16px; color: {c.text}; }}"
        f"QPushButton:hover {{ background: {c.button_hover}; }}"
    )

    # ── 保存按钮 ──
    s["SAVE_BUTTON"] = (
        f"QPushButton {{ background: {c.accent}; color: white; border: none; border-radius: 7px;"
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
        f"QWidget#chatDialog {{ background: {c.window}; color: {c.text}; border: none; border-radius: 12px; "
        f"font-family: {SYSTEM_FONT}; font-size: 13px; }}"
    )

    # ── 标题栏图标/名称 ──
    s["TITLE_ICON"] = "font-size: 18px; background: none;"
    s["TITLE_NAME"] = f"font-weight: bold; font-size: 13px; background: none; color: {c.text};"

    # ── 模型下拉框（窄版）──
    s["MODEL_COMBO_BOX"] = (
        f"QComboBox {{ border: 1px solid {c.border}; border-radius: 7px; padding: 3px 7px;"
        f"  font-size: 11px; color: {c.text}; background: {c.button}; }}"
        f"QComboBox::drop-down {{ border: none; }}"
        f"QComboBox QAbstractItemView {{"
        f"  color: {c.text}; background: {c.surface_elevated};"
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
        f"QLineEdit {{ border: 1px solid {c.border}; border-radius: 7px;"
        f"padding: 5px 8px; color: {c.text}; background: {c.surface_elevated}; }}"
        f"QLineEdit:focus {{ border: 2px solid {c.focus_ring}; padding: 4px 7px; }}"
    )

    # ── 表单多行输入 ──
    s["FORM_TEXTAREA"] = (
        f"QPlainTextEdit {{ border: 1px solid {c.border}; border-radius: 8px; padding: 7px 9px;"
        f"font-size: 12px; font-family: Menlo, monospace; color: {c.text}; background: {c.surface_elevated}; }}"
        f"QPlainTextEdit:focus {{ border: 2px solid {c.focus_ring}; padding: 6px 8px; }}"
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
        f"QPlainTextEdit {{ border: 1px solid {c.border}; border-radius: 10px;"
        f"padding: 8px 10px; font-size: 13px; background: {c.surface_elevated}; color: {c.text}; }}"
        f"QPlainTextEdit:focus {{ border: 2px solid {c.focus_ring}; padding: 7px 9px; }}"
        f"QPlainTextEdit:disabled {{ {disabled} }}"
    )

    # ── 工具栏（标题栏下方）──
    s["TOOLBAR"] = (
        f"background: {c.surface_elevated}; border: none; border-bottom: 1px solid {c.separator};"
    )

    s["EMPTY_STATE"] = (
        f"color: {c.text_secondary}; background: transparent; font-size: {TOKENS.font_body}px; padding: 24px;"
    )
    s["SECTION_TITLE"] = (
        f"color: {c.text_secondary}; background: transparent; font-size: 11px; font-weight: 600;"
    )
    s["FORM_GROUP"] = (
        f"QGroupBox {{ background: {c.surface_elevated}; border: 1px solid {c.separator}; border-radius: 10px; "
        f"margin-top: 24px; padding: 10px; color: {c.text}; }}"
        f"QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 4px; top: 3px; "
        f"padding: 0; background: transparent; color: {c.text_secondary}; font-size: 12px; font-weight: 600; }}"
    )
    s["STATUS_TEXT"] = f"color: {c.text_secondary}; background: transparent; font-size: 11px;"

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
