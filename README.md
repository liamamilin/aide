<h1 align="center">AI 桌面助手</h1>
<p align="center"><em>macOS 常驻 AI 助手 — 选中文字 → <code>⌘⌃L</code> → 一键提问，本地 LLM 零上传。</em></p>

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/UI-PyQt5-green.svg)](https://pypi.org/project/PyQt5/)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-orange.svg)](https://ollama.com)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()

---

## 三入口

| 入口 | 交互 |
|------|------|
| **快捷键** `⌘⌃L` | 任意 App 选中文字 → 按快捷键 → 自动填入对话框 |
| **悬浮按钮** | 屏幕右侧圆形图标，左键开关对话框，右键菜单 |
| **菜单栏图标** | macOS 菜单栏常驻，左键开关对话框，右键快速切换 Agent |

---

## 安装与启动

**前置条件**：[Ollama](https://ollama.com) 已安装并拉取模型：

```bash
ollama serve
ollama pull qwen3:14b
```

**安装**：

```bash
cd /path/to/AI桌面助手
pip install -e .
```

**启动**：

```bash
aide
```

---

## 功能特性

### 对话
- **流式输出** — token 级实时渲染，50ms 批量刷新
- **思考过程** — LLM 推理过程实时展示，完成后折叠为 `💭 思考过程`
- **Markdown 渲染** — 代码块、列表、粗体、标题完整支持
- **多轮对话** — 追问、修正，SQLite 持久化
- **中断 ⏹** — 流式生成时可随时停止
- **编辑 ✏️** — 用户消息 hover 可见编辑按钮，点击回填输入框重新发送
- **复制 📋** — 助手回复 hover 可见复制按钮
- **导出** — 一键复制整段对话为 Markdown
- **通知** — 回复完成时，若窗口在后台则弹 macOS 通知

### Agent
- **3 个内置 Agent**：代码专家 💻 / 翻译 🌐 / 通用助手 🤖
- **自定义 Agent** — 新增/编辑/删除，自定义 emoji 图标和 prompt
- **菜单栏快速切换** — 菜单栏图标右键菜单直接切换 Agent

### 历史
- **对话浏览** — 打开历史窗口，显示所有对话及消息数
- **全文搜索** — 搜索对话标题和消息内容，300ms 防抖
- **删除/加载** — 从历史加载对话，或删除不需要的

### 设置
- **运行时配置** — 右键悬浮按钮 → 设置… → 修改 Ollama 地址、超时、上下文窗口、快捷键
- **持久化** — 所有设置、Agent、模型选择重启后保留
- **自动恢复** — 启动时自动加载上次对话，保留用户选择的 Agent

---

## 配置

运行时通过**设置面板**修改，或编辑 `ai_desktop/config.py`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `OLLAMA_MODEL` | `sorc/qwen3.5-instruct-uncensored:9b` | 默认模型 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `HOTKEY` | `<cmd>+<ctrl>+l` | 全局快捷键 |
| `OLLAMA_NUM_PREDICT` | `20480` | 最大输出 token |
| `OLLAMA_NUM_CTX` | `40960` | 上下文窗口 |
| `OLLAMA_KEEP_ALIVE` | `30m` | 模型驻留时长 |
| `OLLAMA_TIMEOUT` | `120` | HTTP 超时（秒） |

快捷键格式：`<cmd>`, `<shift>`, `<ctrl>`, `<alt>`。不要用 `<fn>` 或 `<option>`。

---

## 故障排除

### 弹窗出现但输入框为空

日志中出现 `This process is not trusted!` 或 `Clipboard unchanged after Cmd+C`：

> macOS 辅助功能权限未授权。

**解决**：系统设置 → 隐私与安全性 → 辅助功能 → 勾选终端应用，必要时取消重勾刷新缓存。

### 启动后无响应

检查 Ollama：`curl http://localhost:11434/api/tags`，或输入栏右侧状态灯 🔴 → 🟢。

---

## 项目结构

```
ai_desktop/
├── main.py                     # 入口 + ChatController
├── config.py                   # LLM/快捷键/Agent 配置
├── capture/
│   ├── hotkey_listener.py      # pynput 全局快捷键 + 运行时切换
│   ├── clipboard_monitor.py    # ⌘C 模拟 → 读取 → 恢复（双回退）
│   └── text_normalizer.py      # 文本清洗 + 截断
├── llm/
│   └── chat_client.py          # Ollama /api/chat（流式 + thinking）
├── ui/
│   ├── float_button.py         # 悬浮圆形按钮（拖拽/跨屏/右键）
│   ├── menubar_icon.py         # macOS 菜单栏图标 + Agent 菜单
│   ├── chat_dialog.py          # 多轮对话 + 快捷键 + 复制/编辑/中断
│   ├── history_dialog.py       # 历史浏览 + 全文搜索
│   ├── agent_editor.py         # Agent 管理（增删改 + emoji 选择）
│   ├── settings_dialog.py      # 运行时设置面板
│   ├── markdown.py             # Markdown → HTML
│   └── styles.py               # QSS 常量
└── utils/
    ├── logging.py              # 日志配置
    └── storage.py              # SQLite 持久化（对话/消息/设置/Agent）
tests/
├── test_smoke.py               # 文本规范化 + Markdown 渲染测试
├── test_storage.py             # DB CRUD 测试（13 项）
└── test_chat_client.py         # 流式解析测试（9 项）
```

---

## 测试

```bash
pytest tests/ -v    # 26 项测试
```
