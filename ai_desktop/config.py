"""
AI 桌面助手 —— 全局配置
"""
from dataclasses import dataclass

# ── LLM ──────────────────────────────────────────────

OLLAMA_BASE_URL: str = "http://localhost:11434"
OLLAMA_MODEL: str = "sorc/qwen3.5-instruct-uncensored:9b"
OLLAMA_TIMEOUT: int = 120
OLLAMA_KEEP_ALIVE: str = "30m"     # 模型保持加载，避免重复加载开销
OLLAMA_NUM_PREDICT: int = 20480     # 限制最大输出 token（含思考 token）
OLLAMA_NUM_CTX: int = 8192         # 上下文窗口大小
OLLAMA_TEMPERATURE: float = 0.7     # 生成温度（0.0 ~ 2.0）
OLLAMA_TOP_P: float = 0.9           # 核采样阈值（0.0 ~ 1.0）
OLLAMA_TOP_K: int = 40              # Top-K 采样
OLLAMA_REPEAT_PENALTY: float = 1.1  # 重复惩罚系数
OLLAMA_MAX_ROUNDS: int = 10         # 发送时保留的最大对话轮次（超出的历史将被截断）
OLLAMA_THINK: bool = False          # 模型是否进行思考推理（对支持 think 的模型生效）

# ── 快捷键 ───────────────────────────────────────────

HOTKEY: str = "<cmd>+<ctrl>+l"

# ── 文本处理 ─────────────────────────────────────────

MAX_TEXT_LENGTH: int = 12_000

# ── 语音朗读 ─────────────────────────────────────────

TTS_MODEL: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-bf16"
TTS_VOICE: str = "Aiden"             # 原生美式英语，优先保证单词发音准确
TTS_LANGUAGE: str = "English"
TTS_MAX_TEXT_LENGTH: int = 500
TTS_IDLE_TIMEOUT: int = 60             # 空闲后卸载模型，释放统一内存

# ── UI ───────────────────────────────────────────────

FONT_FAMILY: str = "PingFang SC, Helvetica, sans-serif"
FONT_SIZE: int = 13

# ── 发布 ─────────────────────────────────────────────

GITHUB_REPO: str = "liamamilin/aide"
UPDATE_CHECK_INTERVAL: int = 86400  # 两次检查间隔（秒），默认 24h

# ── Agent 对话配置 ───────────────────────────────────


@dataclass
class Agent:
    """一个对话 Agent：系统角色 + 专用 Prompt"""
    id: str
    name: str
    icon: str  # emoji
    system_prompt: str


AGENTS: list[Agent] = [
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
    Agent(
        id="summarizer",
        name="摘要",
        icon="📄",
        system_prompt="""你是一个高效摘要助手。

规则：
- 用 1-3 句话概括原文核心内容
- 总字数控制在 100 字以内
- 抓住关键结论，忽略细节
- 用中文输出"""
    ),
    Agent(
        id="polisher",
        name="润色",
        icon="✍️",
        system_prompt="""你是一个文字润色助手。

规则：
- 保持原意不变，优化措辞和节奏
- 修正语法错误和不通顺的句子
- 不添加原文没有的内容
- 如果原文已经很好，直接回复"原文已经很通顺"
- 用和原文相同的语言输出"""
    ),
]

DEFAULT_AGENT_INDEX: int = 1
