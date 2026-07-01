"""
集中式 QSS 样式

所有 UI 文件的 QSS 字符串统一在此定义，便于维护和主题切换。
使用 palette() 语义跟随系统亮/暗主题。
"""

# ── 菜单 ──
MENU = (
    "QMenu { background: palette(window); border: 1px solid palette(mid); "
    "border-radius: 6px; padding: 4px 0; color: palette(text); }"
    "QMenu::item { padding: 6px 24px; font-size: 13px; color: palette(text); }"
    "QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); border-radius: 4px; }"
    "QMenu::separator { height: 1px; background: palette(mid); margin: 4px 10px; }"
)

# ── 主按钮 ──
BUTTON_PRIMARY = """
    QPushButton {
        background: #007AFF;
        color: white;
        border: none;
        border-radius: 5px;
        padding: 6px 14px;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background: #0066d6;
    }
    QPushButton:pressed {
        background: #0055b3;
    }
"""

# ── 标题栏 ──
TITLE_BAR = (
    "background: palette(midlight); border: none; "
    "border-top-left-radius: 10px; border-top-right-radius: 10px; color: palette(text);"
)

# ── 关闭按钮 ──
CLOSE_BUTTON = (
    "QPushButton { background: transparent; border: none; font-size: 16px; color: palette(mid); }"
    "QPushButton:hover { color: palette(text); }"
)

# ── 次要按钮（灰色小按钮：新建对话、历史、导出等）──
SECONDARY_BUTTON = (
    "QPushButton { background: palette(midlight); border: none; border-radius: 5px;"
    "padding: 2px 10px; font-size: 11px; color: palette(text); }"
    "QPushButton:hover { background: palette(mid); }"
)

# ── 输入框 ──
INPUT_FIELD = (
    "QLineEdit { border: 1px solid palette(mid); border-radius: 6px; padding: 6px 10px;"
    "font-size: 13px; background: palette(window); color: palette(text); }"
    "QLineEdit:focus { border-color: #007AFF; }"
)

# ── 下拉选择框 ──
COMBO_BOX = (
    "QComboBox { border: 1px solid palette(mid); border-radius: 5px; padding: 2px 6px;"
    "  font-size: 11px; color: palette(text); background: palette(window); }"
    "QComboBox::drop-down { border: none; }"
    "QComboBox QAbstractItemView {"
    "  color: palette(text);"
    "  background: palette(window);"
    "  selection-background-color: #FFD700;"
    "  selection-color: palette(text);"
    "  outline: none;"
    "}"
)

# ── 滚动区域 ──
SCROLL_AREA = (
    "QScrollArea { border: none; background: palette(window); }"
    "QScrollBar:vertical { width: 6px; }"
    "QScrollBar::handle:vertical { background: palette(mid); border-radius: 3px; }"
)

# ── 消息列表容器 ──
MESSAGE_LIST = "background: palette(window);"

# ── 底部输入栏 ──
INPUT_BAR = (
    "background: palette(midlight); border: none; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;"
)

# ── 对话框基础样式 ──
DIALOG_BASE = "QDialog { background: palette(window); color: palette(text); border: none; border-radius: 10px; }"

# ── 删除按钮（红色 hover）──
DELETE_BUTTON = (
    "QPushButton { background: palette(midlight); border: none; border-radius: 4px;"
    "padding: 2px 8px; font-size: 11px; color: #c00; }"
    "QPushButton:hover { background: palette(mid); }"
)

# ── 表单输入框 ──
FORM_WIDGET = (
    "border: 1px solid palette(mid); border-radius: 4px; padding: 3px 6px;"
    "background: palette(window); color: palette(text);"
)

# ── Ollama 状态指示灯 ──
OLLAMA_STATUS = "font-size: 8px; color: palette(mid); background: none;"
OLLAMA_STATUS_OK = "font-size: 8px; color: #4CAF50; background: none;"
OLLAMA_STATUS_ERR = "font-size: 8px; color: #F44336; background: none;"

# ── 停止按钮（红色）──
STOP_BUTTON = (
    "QPushButton { background: #e02020; color: white; border: none; border-radius: 5px;"
    "padding: 6px 14px; font-size: 12px; font-weight: bold; }"
    "QPushButton:hover { background: #c01010; }"
)

# ── 齿轮/收起按钮（透明 hover）──
ICON_BUTTON = (
    "QPushButton { background: transparent; border: none; font-size: 14px; color: palette(mid); }"
    "QPushButton:hover { color: palette(text); background: palette(midlight); border-radius: 12px; }"
)

# ── 用户消息气泡 ──
USER_BUBBLE = (
    "QLabel { background: #007AFF; color: white; border: none; border-radius: 10px;"
    "padding: 8px 12px; font-size: 13px; }"
)

# ── 助手消息气泡 ──
ASSISTANT_BUBBLE = (
    "QLabel { background: palette(midlight); color: palette(text); border: none; border-radius: 10px;"
    "padding: 8px 12px; font-size: 13px; }"
)

# ── 编辑按钮（用户消息 hover）──
EDIT_BUTTON = """
    QPushButton {
        background: transparent; border: none; font-size: 10px; color: rgba(255,255,255,0.7);
    }
    QPushButton:hover {
        color: white; background: rgba(255,255,255,0.15); border-radius: 3px;
    }
"""

# ── 复制按钮（助手消息 hover）──
COPY_BUTTON = """
    QPushButton {
        background: transparent; border: none; font-size: 11px; color: palette(mid);
    }
    QPushButton:hover {
        color: palette(text); background: rgba(0,0,0,0.06); border-radius: 3px;
    }
"""

# ── 历史行 hover ──
HISTORY_ROW = (
    "QWidget { background: transparent; border: none; border-radius: 6px; }"
    "QWidget:hover { background: palette(midlight); }"
)

# ── 历史删除按钮 ──
HISTORY_DELETE_BUTTON = (
    "QPushButton { background: transparent; border: none; font-size: 12px; }"
    "QPushButton:hover { background: rgba(0,0,0,0.08); border-radius: 12px; }"
)

# ── 搜索框（无边框）──
SEARCH_FIELD = (
    "QLineEdit { border: none; border-bottom: 1px solid palette(mid); padding: 6px 12px;"
    "font-size: 13px; background: palette(window); color: palette(text); }"
    "QLineEdit:focus { border-bottom-color: #007AFF; }"
)

# ── 设置滚动区域 ──
SETTINGS_SCROLL = (
    "QScrollArea { border: none; background: transparent; }"
    "QScrollBar:vertical { width: 6px; background: transparent; }"
    "QScrollBar::handle:vertical { background: palette(mid); border-radius: 3px; min-height: 20px; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
)

# ── 取消按钮 ──
CANCEL_BUTTON = (
    "QPushButton { background: palette(midlight); border: none; border-radius: 4px;"
    "padding: 6px 16px; color: palette(text); }"
    "QPushButton:hover { background: palette(mid); }"
)

# ── 保存按钮（蓝色）──
SAVE_BUTTON = (
    "QPushButton { background: #007AFF; color: white; border: none; border-radius: 4px;"
    "padding: 6px 16px; }"
    "QPushButton:hover { background: #0066d6; }"
)

# ── Emoji 选择按钮 ──
EMOJI_PICK_BUTTON = (
    "QPushButton { background: palette(midlight); border: 1px solid palette(mid); border-radius: 4px;"
    "font-size: 14px; color: palette(text); }"
    "QPushButton:hover { background: palette(mid); }"
)

# ── Emoji 网格按钮 ──
EMOJI_GRID_BUTTON = (
    "QPushButton { font-size: 17px; border: 1px solid palette(mid); border-radius: 4px; background: palette(window); }"
    "QPushButton:hover { background: palette(midlight); border-color: #007AFF; }"
)

# ── 对话窗口根样式 ──
CHAT_DIALOG_ROOT = "QWidget { background: palette(window); color: palette(text); border: none; border-radius: 10px; }"

# ── 标题栏图标/名称 ──
TITLE_ICON = "font-size: 18px; background: none;"
TITLE_NAME = "font-weight: bold; font-size: 13px; background: none; color: palette(text);"

# ── 模型下拉框（窄版）──
MODEL_COMBO_BOX = (
    "QComboBox { border: 1px solid palette(mid); border-radius: 5px; padding: 2px 4px;"
    "  font-size: 11px; color: palette(text); background: palette(window); }"
    "QComboBox::drop-down { border: none; }"
    "QComboBox QAbstractItemView {"
    "  color: palette(text);"
    "  background: palette(window);"
    "  selection-background-color: #FFD700;"
    "  selection-color: palette(text);"
    "  outline: none;"
    "  min-width: 120px;"
    "}"
)

# ── Agent 编辑按钮 ──
AGENT_EDIT_BUTTON = (
    "QPushButton { background: palette(midlight); border: none; border-radius: 4px;"
    "padding: 2px 8px; font-size: 11px; color: palette(text); }"
    "QPushButton:hover { background: palette(mid); }"
)

# ── 表单输入框（带 focus 高亮）──
FORM_INPUT = (
    "QLineEdit { border: 1px solid palette(mid); border-radius: 4px;"
    "padding: 4px 8px; color: palette(text); background: palette(window); }"
    "QLineEdit:focus { border-color: #007AFF; }"
)

# ── 表单多行输入 ──
FORM_TEXTAREA = (
    "QPlainTextEdit { border: 1px solid palette(mid); border-radius: 4px; padding: 6px 8px;"
    "font-size: 12px; font-family: Menlo, monospace; color: palette(text); background: palette(window); }"
    "QPlainTextEdit:focus { border-color: #007AFF; }"
)

# ── 标签文字 ──
LABEL = "color: palette(text); background: none;"
LABEL_SECONDARY = "font-size: 11px; color: palette(mid); background: none;"
LABEL_BOLD = "font-weight: bold; font-size: 13px; background: none; color: palette(text);"

# ── 新增 Agent 按钮 ──
ADD_AGENT_BUTTON = (
    "QPushButton { background: #007AFF; color: white; border: none; border-radius: 5px;"
    "padding: 2px 14px; font-size: 12px; }"
    "QPushButton:hover { background: #0066d6; }"
)

# ── Agent 列表底部栏 ──
AGENT_LIST_BAR = (
    "background: palette(midlight); border: none; "
    "border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;"
)

# ── 透明背景 ──
TRANSPARENT = "background: transparent;"
