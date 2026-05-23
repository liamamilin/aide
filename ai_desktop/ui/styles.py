"""
集中式 QSS 样式
"""
from ai_desktop import config

FONT_FAMILY = config.FONT_FAMILY
FONT_SIZE = config.FONT_SIZE

BASE = f"""
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_SIZE}px;
    color: #1a1a1a;
"""

PANEL = f"""
    QWidget {{
        background: #ffffff;
        border: 1px solid #c8c8c8;
        border-radius: 10px;
    }}
"""

SCROLL_AREA = f"""
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        width: 6px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        background: #c0c0c0;
        border-radius: 3px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0;
    }}
"""

BUTTON = """
    QPushButton {
        background: #f0f0f0;
        border: 1px solid #d0d0d0;
        border-radius: 5px;
        padding: 6px 14px;
        font-size: 12px;
    }
    QPushButton:hover {
        background: #e4e4e4;
    }
    QPushButton:pressed {
        background: #d6d6d6;
    }
"""

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
