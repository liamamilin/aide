# AI 桌面助手

> macOS 常驻 AI 助手 — 选中文字 → `⌘⌃L` → 一键提问，本地 LLM 零上传。

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/UI-PyQt5-green.svg)](https://pypi.org/project/PyQt5/)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-orange.svg)](https://ollama.com)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()

---

## 目录

- [安装与启动](#安装与启动)
- [使用方式](#使用方式)
- [Agent 切换](#agent-切换)
- [模型选择](#模型选择)
- [功能特性](#功能特性)
- [配置](#配置)
- [故障排除](#故障排除)
- [项目结构](#项目结构)

---

## 安装与启动

**前置条件**：[Ollama](https://ollama.com) 已安装并拉取模型：

```bash
ollama serve              # 启动服务
ollama pull qwen3:14b     # 拉取模型（一次）
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

启动后，桌面右侧出现圆形悬浮按钮，表示已就绪。

---

## 使用方式

| 触发 | 操作 |
|------|------|
| **快捷键** | 任意 App 中选中文字 → `⌘⌃L` → 自动弹出对话窗口，文字已粘贴 → `Enter` 发送 |
| **悬浮按钮** | 点击桌面右侧圆形按钮 → 打开对话窗口 → 输入问题 → `Enter` 发送 |

悬浮按钮支持**拖拽**移动，自动跟随鼠标跨屏幕、跨 Space。

---

## Agent 切换

窗口左上角下拉框可随时切换 Agent，仅影响新消息，不影响已有对话：

| Agent | 图标 | 行为 |
|-------|------|------|
| **代码专家** | 💻 | 审查代码、debug、重构、写代码 |
| **翻译** | 🌐 | 中英互译、润色表达 |
| **通用助手** | 🤖 | 兜底通用问答 |

---

## 模型选择

窗口左上角模型下拉框列出本地所有已拉取的 Ollama 模型，随时切换。

| 模型 | 速度 | 质量 | 推荐场景 |
|------|------|------|----------|
| `qwen3:4b` | 快 | 一般 | 翻译、简单问答 |
| `qwen3:8b` | 中 | 好 | 日常使用推荐 |
| `qwen3:14b` | 慢 | 最佳 | 代码审查、复杂分析 |

> 上次选择的 Agent 和模型会自动记住，下次启动恢复。

---

## 功能特性

- **全局快捷键** — `⌘⌃L` 跨 App 触发，无需切窗口
- **思考过程可见** — LLM 推理 token 实时流式显示，最终折叠为 `💭 思考过程` 可展开块
- **多轮对话** — 支持追问、修正，历史自动存入本地 SQLite
- **流式输出** — Token 级实时渲染，50ms 批量刷新 UI 无卡顿
- **Markdown 渲染** — 代码块、列表、粗体、标题完整支持
- **剪贴板安全** — 捕获选中文字时自动保存并恢复原始剪贴板
- **跨屏幕 / 跨 Space** — 悬浮按钮跟随鼠标所在屏幕和虚拟桌面
- **右键菜单** — 悬浮按钮右键：隐藏、关于、退出
- **偏好记忆** — Agent 和模型选择持久化，重启恢复

---

## 配置

编辑 `ai_desktop/config.py`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `OLLAMA_MODEL` | `qwen3:14b` | 默认模型（需先 `ollama pull`） |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `HOTKEY` | `<cmd>+<ctrl>+l` | 全局快捷键 |
| `OLLAMA_NUM_PREDICT` | `2048` | 最大输出 token |
| `OLLAMA_NUM_CTX` | `4096` | 上下文窗口 token 数 |
| `OLLAMA_KEEP_ALIVE` | `30m` | 模型驻留内存时长 |
| `OLLAMA_TIMEOUT` | `120` | HTTP 超时（秒） |
| `MAX_TEXT_LENGTH` | `12000` | 选中文字最大截取长度 |

### 快捷键格式说明

pynput 格式，修饰键用尖括号：

| 按键 | 写法 | | 按键 | 写法 |
|------|------|-|------|------|
| ⌘ Command | `<cmd>` | | ⌃ Control | `<ctrl>` |
| ⇧ Shift   | `<shift>` | | ⌥ Option | `<alt>` |

> **注意**：macOS 上不要用 `<fn>`、`<option>`、`<command>` 全称，pynput 不支持。

---

## 故障排除

### 弹窗出现但输入框为空

日志中出现 `This process is not trusted!` 或 `Clipboard unchanged after Cmd+C`：

**原因**：macOS 辅助功能权限未授权，`pynput` 无法模拟 `⌘C` 按键。

**解决**：
1. 打开 **系统设置 → 隐私与安全性 → 辅助功能**
2. 在列表中找到你的终端应用（Terminal.app 或 iTerm.app），确保已勾选
3. 如已勾选仍无效：**先取消勾选，再重新勾选**（刷新 TCC 权限缓存）
4. 重启 `aide`

### 启动后无响应

检查 Ollama 是否正在运行：

```bash
curl http://localhost:11434/api/tags
```

如无响应，先启动 Ollama：`ollama serve`

---

## 项目结构

```
ai_desktop/
├── main.py                    # 入口 + ChatController（总控）
├── config.py                  # LLM / 快捷键 / Agent 配置
├── capture/
│   ├── hotkey_listener.py     # pynput 全局快捷键监听
│   ├── clipboard_monitor.py   # ⌘C 模拟 → 读取 → 恢复剪贴板
│   └── text_normalizer.py     # 文本清洗 + 截断
├── llm/
│   └── chat_client.py         # Ollama /api/chat（流式 + thinking）
├── ui/
│   ├── float_button.py        # 悬浮圆形按钮（拖拽/跨屏/右键菜单）
│   ├── chat_dialog.py         # 多轮对话窗口（Agent/模型切换）
│   ├── markdown.py            # Markdown → HTML 内联渲染
│   └── styles.py              # 全局 QSS 样式常量
└── utils/
    ├── logging.py             # 日志配置
    └── storage.py             # SQLite 持久化（对话 + 设置）
```

### 数据流

```
选中文字 → ⌘⌃L (pynput 后台线程)
    │
    ▼
模拟 ⌘C → 读剪贴板 → 恢复原剪贴板 (text captured)
    │
    ▼ pyqtSignal → Qt 主线程
打开对话窗口 + 粘贴文字到输入框
    │
    ▼ Enter
Ollama /api/chat (stream + thinking tokens)
    │
    ▼
流式渲染 Markdown，thinking 可折叠
```
