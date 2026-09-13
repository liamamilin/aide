<h1 align="center">AI 桌面助手</h1>
<p align="center"><em>macOS 常驻 AI 助手 — 选中文字 → <code>⌘⌃L</code> → 一键提问，<code>⌘⌃S</code> 框选截图提问，本地 LLM 零上传。</em></p>

<p align="center"><strong>中文</strong> | <a href="README.md">English</a></p>

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

## 下载安装

### 方式一：DMG 安装（推荐）

1. 前往 [Releases](https://github.com/liamamilin/aide/releases) 下载最新 `AI桌面助手-*.dmg`
2. 双击 DMG，将 `AI桌面助手` 拖入 Applications
3. 首次启动需在系统设置 → 隐私与安全性中允许运行
4. 确保 [Ollama](https://ollama.com) 已安装并运行

### 方式二：源码安装

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

**开发**（运行测试需要）：

```bash
pip install -r requirements-dev.txt
```

**启动**：

```bash
aide
```

**构建 `.app` 安装包**（需要 PyInstaller）：

```bash
# 快速构建（清理 → 构建 → 签名）
./scripts/build.sh

# 构建前跑 ruff + pytest
./scripts/build.sh --test

# 构建 + 冒烟测试
./scripts/build.sh --smoke

# 构建 + 生成 DMG 安装包
./scripts/build.sh --dmg

# 全套
./scripts/build.sh --test --smoke --dmg
```

`ai_desktop/version.py` 是 Python 包、运行时、`Info.plist` 与 DMG 文件名的唯一版本来源。本地构建默认使用 ad-hoc 签名；生成开发者签名的分发候选包时，通过 `AIDE_SIGN_IDENTITY` 指定 Developer ID。只读预检不会提交、打标签或推送：

```bash
python scripts/release_check.py --version 1.5.0 --require-new-tag
```

---

## 功能特性

### 对话
- **流式输出** — token 级实时渲染，50ms 批量刷新
- **思考过程** — LLM 推理过程实时展示，完成后折叠为 `💭 思考过程`
- **Markdown 渲染** — 代码块、列表、粗体、标题完整支持，标题使用强调色蓝色
- **多轮对话** — 追问、修正，SQLite 持久化
- **图片理解（多模态）** — 粘贴 / 拖拽 / 📎 附件 / `⌘⌃S` 框选截图四种方式发图，附件经过真实解码校验和受管生命周期处理，历史缺图可识别，并显示当前模型的图片能力状态
- **中断 ⏹** — 流式生成时可随时停止
- **编辑 ✏️** — 用户消息 hover 可见编辑按钮，点击回填输入框重新发送
- **复制 📋** — 助手回复 hover 可见复制按钮
- **输入框自动伸缩** — 多行输入时高度自动从 36px 扩至 120px，超长可滚动
- **导出** — 一键复制整段对话为 Markdown
- **通知** — 回复完成时，若窗口在后台则弹 macOS 通知

### Agent
- **5 个内置 Agent**：代码专家 💻 / 翻译 🌐 / 通用助手 🤖 / 摘要 📄 / 润色 ✍️
- **自定义 Agent** — 新增/编辑/删除，自定义 emoji 图标和 prompt
- **任务模型配置** — 为每个 Agent 绑定可复用的模型、思考模式、温度和输出上限；每一项都可继承全局设置
- **菜单栏快速切换** — 菜单栏图标右键菜单直接切换 Agent

### 历史
- **对话浏览** — 稳定分页访问全部对话并显示消息数
- **全文搜索** — 搜索对话标题和消息内容，300ms 防抖
- **重命名/删除/加载** — 校验并修改标题，从历史加载对话，或删除不需要的

### 设置
- **运行时配置** — 右键悬浮按钮 → 设置… → 修改 Ollama 地址、超时、上下文窗口、快捷键
- **持久化** — 设置、Agent、模型选择、聊天窗口尺寸位置和悬浮按钮位置重启后保留；屏幕变化时自动恢复到可用区域
- **自动恢复** — 启动时自动加载上次对话，保留用户选择的 Agent

### 数据库升级与恢复

升级已有的无版本数据库前，应用会通过 SQLite Backup API 在数据目录的 `backups/` 中创建一致备份。全部结构变更在同一事务提交；迁移失败时保留原 schema 和数据。恢复匹配旧版本的备份前先退出应用：

```bash
python scripts/restore_database.py "/path/to/backup.sqlite3"
```

恢复工具会先把当前数据库另存为 `before-restore` 安全副本。恢复旧 schema 后若再次启动新版应用，它会重新执行升级；需要降级时，应启动与该备份匹配的旧版应用。

---

## 配置

运行时通过**设置面板**修改，或编辑 `ai_desktop/config.py`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `OLLAMA_MODEL` | `sorc/qwen3.5-instruct-uncensored:9b` | 默认模型（不存在时自动切到第一个可用模型） |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `HOTKEY` | `<cmd>+<ctrl>+l` | 全局快捷键 |
| `OLLAMA_NUM_PREDICT` | `20480` | 最大输出 token |
| `OLLAMA_NUM_CTX` | `8192` | 上下文窗口 |
| `OLLAMA_TEMPERATURE` | `0.7` | 生成温度（0.0 ~ 2.0） |
| `OLLAMA_TOP_P` | `0.9` | 核采样阈值 |
| `OLLAMA_TOP_K` | `40` | Top-K 采样 |
| `OLLAMA_REPEAT_PENALTY` | `1.1` | 重复惩罚系数 |
| `OLLAMA_THINK` | `True` | 模型思考推理开关 |
| `OLLAMA_MAX_ROUNDS` | `10` | 保留的最大对话轮次 |
| `OLLAMA_KEEP_ALIVE` | `30m` | 模型驻留时长 |
| `OLLAMA_TIMEOUT` | `120` | HTTP 超时（秒） |
| `SCREENSHOT_HOTKEY` | `<cmd>+<ctrl>+s` | 框选截图并发送到对话（需屏幕录制权限） |

快捷键格式：`<cmd>`, `<shift>`, `<ctrl>`, `<alt>`。不要用 `<fn>` 或 `<option>`。

---

## 故障排除

### 弹窗出现但输入框为空

日志中出现 `This process is not trusted!` 或 `Clipboard unchanged after Cmd+C`：

> macOS 辅助功能权限未授权。

**解决**：系统设置 → 隐私与安全性 → 辅助功能 → 勾选终端应用，必要时取消重勾刷新缓存。

### 启动后无响应

检查 Ollama：`curl http://localhost:11434/api/tags`，或输入栏右侧状态灯 🔴 → 🟢。

### 截图失败或无截图

点击 📎 → 截图或按 `⌘⌃S` 后提示「截图失败」/无反应：

> macOS 屏幕录制权限未授权。

**解决**：系统设置 → 隐私与安全性 → 屏幕录制 → 勾选「AI 桌面助手」（开发模式为「终端」），重启应用后生效。未授权时应用会弹窗引导并可直接跳转设置页。

### 发图后模型不识别图片内容

当前模型不支持视觉（多模态）。**解决**：在下拉框切换为视觉模型（如 `llava`、`qwen2.5vl`）后重发。

---

## 项目结构

```
ai_desktop/
├── main.py                     # 入口 + ChatController
├── config.py                   # LLM/快捷键/Agent 配置
├── __main__.py                 # python -m ai_desktop 支持
├── version.py                  # 应用唯一版本来源
├── settings_manager.py         # 配置持久化加载/应用
├── agent_manager.py            # Agent 列表管理/切换/保存
├── capture/
│   ├── hotkey_listener.py      # pynput 全局快捷键（dev 模式）
│   ├── nsevent_monitor.py      # NSEvent 全局监听（frozen 模式）
│   ├── screenshot.py           # screencapture 框选截图（权限错误识别）
│   ├── clipboard_monitor.py    # ⌘C 模拟 → 读取 → 恢复（双回退）
│   └── text_normalizer.py      # 文本清洗 + 截断
├── llm/
│   └── chat_client.py          # Ollama /api/chat（流式 + thinking + 多模态 images）
├── ui/
│   ├── float_button.py         # 悬浮圆形按钮（拖拽/跨屏/右键）
│   ├── menubar_icon.py         # macOS 菜单栏图标 + Agent 菜单
│   ├── chat_dialog.py          # 多轮对话 + 快捷键 + 复制/编辑/中断 + 图片收发
│   ├── history_dialog.py       # 历史浏览 + 全文搜索
│   ├── agent_editor.py         # Agent 管理（增删改 + emoji 选择）
│   ├── settings_dialog.py      # 运行时设置面板
│   ├── markdown.py             # Markdown → HTML
│   ├── styles.py               # QSS 常量（48 条集中管理，懒加载）
│   ├── theme.py                # ColorSet 显式亮/暗色值
│   ├── frameless_mixin.py      # FramelessDragMixin + TitleBar
│   └── __init__.py
├── utils/
│   ├── images.py               # 图片存储/base64/类型判断
│   ├── logging.py              # 日志配置
│   ├── storage.py              # SQLite 持久化（对话/消息/设置/Agent/图片路径）
│   └── permissions.py          # AX + 输入监听权限检测/请求
scripts/
├── aide.spec                   # 本地与 CI 共用的 PyInstaller 定义
├── build.sh                    # 构建、签名、验证、冒烟、打包
├── build_dmg.sh                # 生成带版本号的 DMG
├── release_check.py            # 只读源码/应用包预检
└── smoke_test.sh               # 隔离启动打包应用
tests/
├── conftest.py                 # 共享 fixture（qapp、存储、替身）
├── fake_ollama.py              # 本地 HTTP 测试服务
└── test_*.py                   # UI、生命周期、传输、数据与发布测试
```

---

## 测试

自动化测试覆盖 UI 状态、请求/会话生命周期、可取消传输、捕获、持久化、主题、入口和发布打包：

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行与 CI 相同的检查
python -m ruff check ai_desktop/ tests/ scripts/release_check.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

测试使用 `pytest-qt` 进行真实的 PyQt5 窗口渲染，外部依赖（Ollama HTTP、剪贴板、macOS ctypes、screencapture）均通过 mock 隔离。
