"""
AI 桌面助手 —— 全局配置
"""
from dataclasses import dataclass
from typing import List

# ── LLM ──────────────────────────────────────────────

OLLAMA_BASE_URL: str = "http://localhost:11434"
OLLAMA_MODEL: str = "sorc/qwen3.5-instruct-uncensored:9b"
OLLAMA_TIMEOUT: int = 120
OLLAMA_KEEP_ALIVE: str = "30m"     # 模型保持加载，避免重复加载开销
OLLAMA_NUM_PREDICT: int = 20480     # 限制最大输出 token（含思考 token）
OLLAMA_NUM_CTX: int = 40960         # 上下文窗口大小

# ── 快捷键 ───────────────────────────────────────────

HOTKEY: str = "<cmd>+<ctrl>+l"

# ── 文本处理 ─────────────────────────────────────────

MAX_TEXT_LENGTH: int = 12_000

# ── UI ───────────────────────────────────────────────

FONT_FAMILY: str = "PingFang SC, Helvetica, sans-serif"
FONT_SIZE: int = 13

# ── Agent 对话配置 ───────────────────────────────────


@dataclass
class Agent:
    """一个对话 Agent：系统角色 + 专用 Prompt"""
    id: str
    name: str
    icon: str  # emoji
    system_prompt: str


AGENTS: List[Agent] = [
    Agent(
        id="general_assistant",
        name="通用助手",
        icon="🤖",
        system_prompt="""你是一个简洁实用的中文助手。

规则：
- 如果不确定，直接说"不确定"
- 用中文回复，尽量简短""",
    ),
    Agent(
        id="code_expert",
        name="代码专家",
        icon="💻",
        system_prompt="""你是一个资深代码专家。

输出格式（按需使用）：
- 问题：一句话概括
- 原因：为什么会出现
- 建议：具体怎么改
- 如果用户让你写代码，直接给出最优实现

规则：
- 代码块用 Markdown 格式（```python 等）
- 有多个方案时给出对比
- 用中文解释，代码保持原样""",
    ),
    Agent(
        id="translator",
        name="翻译",
        icon="🌐",
        system_prompt="""你是一个中英翻译专家。  

规则：
- 首先给出翻译结果
- 可以加入关键知识点讲解, 报错词性(名词还是动词?), 形变(名词形式是什么?动词?), 语意, 用法, 搭配, 例句
- 如果原文有明显错误，翻译后在括号内简注
- 总字数 150字以内
"""
    ),
]

DEFAULT_AGENT_INDEX: int = 1
