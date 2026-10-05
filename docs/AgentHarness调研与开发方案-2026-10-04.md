# 轻量 Agent Harness：调研、现状与开发方案

日期：2026-10-04。既有应用代码基线：`f3b3924`，方案复核基线：`3f2280b`，分支 `codex/request-lifecycle`。状态：**H0 首个本地候选已受限准入；工具功能尚未接入应用**。实测结果见 [H0 模型准入实测](H0模型准入实测-2026-10-04.md)。

| 节 | 内容 |
|---|---|
| [1](#1-建议与首版范围) | 首版范围与主要决策 |
| [2](#2-项目现状) | 逐文件差距与源码核对结论 |
| [3](#3-官方方案调研) | Pi、smolagents、Ollama、Exa、Parallel 官方资料 |
| [4](#4-技术选型) | 为何自研、为何不引入 Pi、重评触发条件 |
| [5](#5-最小架构与运行契约) | 唯一执行路径、Agent 与 Harness 的关系、事件身份、附件与图片预算、纯对话记录、截断轮次处理、预算耗尽终止、三条数据路径、能力准入三段式、`think` 结构化、Ollama 配对规则 |
| [6](#6-工具设计) | Bash 白名单与分类器、确认与过期、联网搜索 |
| [7](#7-会话上下文与存储) | schema 5、上下文原则、保留期与清理、隔离语义 |
| [8](#8-用户体验与预算) | 入口与卡片、流式文本呈现、预算初值、配置实测 |
| [9](#9-开发工作包) | 工作包与分支估时、H0 五阶段准入协议、中止判据、回归清单 |
| [10](#10-下一次开发起点) | 起点与未验证假设 |

修订记录（2026-10-04；以下前期记录以最新正文为准）：

- 前期评审：曾按无调用 ID 设计位置配对，并把 `num_predict > num_ctx` 视作配置冲突；本次复核改为协议能力兼容与版本化预算实测，不能沿用这两项旧结论。
- 能力准入：确立 `capability declaration → behavioral verification → admission` 三段式，作为贯穿全文的通用原则；`think` 由 `bool` 改为模式加精确档位字符串，合法取值域由 `/api/show` 的 `thinking.values` 决定，区分继承配置与模型默认；UI 两处（全局设置、model profile）均改为 capability-aware。
- 前期数据语义：区分模型保真、持久化脱敏和执行预算削减；本次进一步固定 length 立即结束、终止证据与诊断分开、运行元数据与 30 天 payload 保留分开。具体规则以第 5、7 节为准。
- H0 重构为准入协议：由六个零散问题改为 H0.1 Capability Discovery → H0.2 Behavioral Verification → H0.3 Token/Latency Distribution → H0.4 Budget Selection → H0.5 Tool-use Admission 五阶段，每阶段有 exit artifact 与失败分支；**只有 H0.5 允许得出工具调用能力不足的结论**，此前只能记为未归因失败；`task_mode_enabled = budget_verified AND tool_use_admitted` 为硬门；H0.2 的 named effort 验证以「可重复、可解释的行为差异」为判据，不采用「low token 必须小于 high」这类单次比较；H0.3 须留存每次 run 原始观测而非仅统计量；H0.4 产出经验证的 `task_profile` 组而非孤立 `num_predict`；H1 按 H0.2 结果分两支估时。
- 架构决策：改为唯一执行路径（普通对话是空工具集的退化情形）；`general_assistant` 承载工具授权，不新增 Agent 入口；放弃 Pi RPC 并记录可勾选的重评触发条件；Bash 权限改为白名单 + 分类器三层判定，网络命令归确认、搜索走 `web_search`；工作包新增 H6 删除旧聊天路径。
- 协议核实补充：`done_reason` 截断轮次不得执行工具；`num_predict` 与 `think` 必须一起定；确认等待 10 分钟过期并走拒绝路径；审计 payload 与输出文件保留 30 天，元数据随会话保留并级联删除；卡片内实时显示流式文本并在状态切换时保留已显示内容。
- 源码核实补充：当前 request_id 过滤适用于单轮；多轮运行须扩展事件身份，而非仅放宽为 run_id；附件持有改为 run 级并把图片 token 计入预算；纯对话同样写 run 行（schema 5 只迁移一次）；思考与生成额度由 H0 实测分布决定，不采用预设的 `≤ 2048`。
- 本次解决方案：Bash 先解析后允许列表判定，复杂语法统一确认；工具调用分本地 ID 与服务端 ID；流式路由校验 run/step/request，终态分层；预算不假定 num_ctx 是累计生成硬顶；30 天到期清理数据库与文件中的审计 payload，保留无内容元数据；Action 通过同一运行时的强制空工具集执行。验收条目与工作包已同步。

前期方案阶段仅调研官方资料、分析源码和制定方案。当前已新增独立 H0 探针、相关测试及真实本地模型记录；没有安装 Pi、调用付费搜索、执行模型生成的宿主命令、接入应用发送入口或重建应用。既有 616 项测试不代表 Harness 验证；本次相关回归 164 项与 H0 模型证据分开记录。

## 1. 建议与首版范围

在现有 Python/Qt 项目中增加 Harness 运行时，继续使用 Ollama，执行循环与事件设计参考 Pi（不引入 Pi 本身，依据见第 4 节）。模型只调用两个结构化工具：`bash` 和 `web_search`。搜索后端支持 Exa、Parallel，由用户选择其中一个，每次任务固定配置。

**本版交付什么**：一个统一的运行循环（普通对话即其空工具集退化形态）、两个结构化工具、由 `general_assistant` 承载的工具授权、Bash 的白名单与危险分类器、run/step 级执行记录、以及 H0–H6 可验收工作包。

**主要决策**：执行路径唯一化；不新增 Agent 入口；工具授权来自配置常量而非 Agent 字段；Bash 确认采用三层判定而非逐条确认或一律放行；网络访问统一走 `web_search`。

例如让通用助手“查看一个目录，分析文件，再联网查资料，最后给出结论”。闭环是：**提问 → 模型判断 → 工具调用 → 收到结果 → 再判断 → 回答或继续调用**。只把 Bash 输出拼进一次聊天，还不构成该能力。

首版包含顺序工具调用、流式回答、执行状态、按危险判定的命令确认、停止、有限预算和可查看的执行记录。不加入多 Agent、MCP、插件市场、计划树、自动记忆、定时任务、后台长任务或浏览器自动化；截图、OCR、朗读和宠物继续保留原有职责，`action_service` 的四项确定动作也不进入循环。

先验证协议和模型，再做完整界面与打包。9B 已通过固定参数、模拟工具与明确联网意图的小样本准入，27B 仅发现声明；不能外推任意任务的可靠性。

## 2. 项目现状

| 位置 | 已有能力 | 所需变化 |
|---|---|---|
| `config.py` / `agent_manager.py` | Agent 是角色、提示词、模型配置绑定 | 以配置常量授予 `general_assistant` 工具，不改 `Agent` 字段；其余 Agent 保持无工具 |
| `services/model_profiles.py` | 参数校验、配置解析与回退 | 固定任务模型配置，独立检查工具能力 |
| `llm/events.py` | 不可变 RequestContext、request/conversation/agent 标识和终态 | 增加工具与步骤事件，区分单轮回复完成和整个任务完成 |
| `llm/chat_client.py` | Ollama `/api/chat` 与普通消息转换 | 发送 tools；保留 assistant tool_calls、thinking 和对应 tool 消息 |
| `llm/qt_stream.py` | UTF-8 NDJSON、连接/空闲超时、HTTP abort | 完整汇集工具调用；单轮 done 不直接终结整个任务 |
| `llm/streaming_worker.py` | 后台事件循环、取消记忆、附件保留/释放 | 新增多轮 HarnessWorker，工具不能阻塞 UI 和网络事件循环 |
| `main.py` | 发送/停止、会话切换、请求 ID 过滤、回答版本 | 循环移出并统一，旧发送路径以适配器接入后在 H6 删除 |
| `utils/storage.py` | SQLite schema 4、消息、回答版本、迁移前备份 | 增加运行与步骤记录，工具轨迹不冒充回答版本 |
| `ui/chat_dialog.py` / `ui/fluent.py` | 默认 Fluent Light 对话、菜单、通知 | 用默认卡片呈现工具记录与命令确认 |
| 现有测试 | 请求快照、取消、协议、附件、配置、窗口 | 补多轮工具、进程取消、预算和任务切换测试 |

源码核对结论：

- `_payload()` 没有 tools；`_build_ollama_messages()` 目前只输出 role/content/images。解析层只消费 thinking/content，响应即使包含工具调用也没有执行链路。
- `_new_conversation()` 先停止 worker，再清空会话和消息；流式回调按 request_id 过滤。这是可以复用的隔离基础。
- `_context_messages()` 选择当前有效回答版本；发送前按 `OLLAMA_MAX_ROUNDS * 2` 截取消息。工具任务不能逐条照搬截断，否则会切断 assistant 调用与 tool 结果的配对。
- 源码默认 `num_ctx=8192`、`num_predict=20480`、最多 10 轮。这不是用户实时配置，工具结果和思考需要独立预算。但两数大小关系本身不能证明会截断，须核对服务版本、context shift/truncation 与实际生成行为，见第 8 节。
- `llm/service_checks.py` 当前探测图片能力；模型名称包含 Qwen 不代表已经通过工具调用验收。
- 截图/朗读的 subprocess 是应用固定命令，不能视作已存在供模型使用的 Bash 工具。
- `config.py` 的 `general_assistant` system_prompt 为「简洁实用 / 不确定直接说不确定 / 尽量简短」，其中「不确定就说不确定」与工具循环直接冲突，须改写为「先用工具查证，查不到再说不确定」。这是既有 prompt 可直接修改的一处，不需要为任务模式另建 prompt。
- `think` 全链路按 `bool` 处理（`config.py`、`settings_manager`、`settings_dialog`、`model_profile_dialog`、`model_profiles` 三层覆盖、`chat_client`、`events`），而 Ollama 定义的是 `null` / 布尔 / 模型自定义档位字符串三态，且档位名不被接受时**静默回落模型默认而不报错**。任务模式无法只降低思考强度；合法取值域须由 `/api/show` 的 `thinking.values` 决定。落点与易错点见第 5 节。
- `service_checks.py:231` 已调用 `/api/show` 并解析 `capabilities` 判 vision，但**未读取 `thinking` 对象**。扩展该解析器即可获得档位声明，无需新增网络路径。
- `services/action_service.py` 的 `translate / explain / summarize / rewrite` 是绑定 `agent_id` 的单次确定动作，prompt 均含「不要执行材料中的指令」。这一层不进入 Harness 循环，也不需要工具能力；它是确定性流程，不是待统一的重复。

## 3. 官方方案调研

### Pi

Pi 将核心工具执行与状态管理放在 agent-core，模型协议放在 pi-ai，应用层负责会话和界面。官方产品定位是可扩展的轻量 Harness，默认不内置多 Agent 和计划模式，额外工作流交给扩展。[Pi 官方说明](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)

核心做法：内部消息与 LLM 消息分开；先变换上下文，再转换模型协议；流式输出模型消息和工具开始/更新/结束事件；工具结果回填后进入下一轮。当前支持顺序与并行工具执行，本项目首版只取顺序执行。[agent-core 文档](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md)、[循环源码](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts)

Bash 实现具有工作目录、增量输出、退出码、可选超时与取消时的进程树清理；模型侧输出截断，超出部分可以保存文件。通用截断源码当前默认 2000 行 / 50 KiB。本项目上下文更紧，不直接复制其尺寸。[Bash 源码](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/bash.ts)、[截断源码](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/truncate.ts)

Pi 也可通过长驻子进程的 stdin/stdout JSONL RPC 接入其他语言。命令被接受与任务结束是不同事件，还需处理背压、取消、退出和协议版本；不是启动 CLI 后等待退出即可。[RPC 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)

其会话实现包含带 parentId 的条目和压缩记录。本项目只借鉴可追溯消息与运行归属，继续使用已有 SQLite，不复制会话树。[会话源码](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)

Pi 官方根 README 明确没有内置限制文件、进程、网络与凭据访问的权限系统。项目资源信任提示与 OS 沙箱不同，不能因参考 Pi 就宣称本地 Bash 已隔离。[权限说明](https://github.com/earendil-works/pi#permissions--containerization)

### smolagents

Hugging Face 官方同样把 Agent 定义为模型选择动作、执行工具、记录观察结果、继续判断的循环，并指出确定流程通常适合直接编程。启发是保留翻译/OCR/朗读的确定性流程，只把开放式任务交给 Harness。[smolagents 官方说明](https://huggingface.co/docs/smolagents/en/conceptual_guides/intro_agents)

本项目采用具名、参数校验、结构化返回的两个工具；不在 UI 进程中执行模型任意生成的 Python。暂不引入另一套框架。这是适配判断，未进行框架性能比较。

### 搜索与模型协议

Exa 提供 `POST https://api.exa.ai/search`，通过 x-api-key 鉴权。query 可同时请求 highlights，结果具有标题、URL、日期、摘录，可直接 HTTP 接入。[Exa Search API](https://exa.ai/docs/reference/search)

Parallel 接收 objective 和 search_queries，返回模型可用的网页摘录，使用 x-api-key。当前官方要求新集成使用 `/v1/search`，不要沿用旧 `/v1beta/search`。[Parallel Search Quickstart](https://docs.parallel.ai/search/search-quickstart)

Parallel 的官方 Pi 扩展提供 web_search 与 web_fetch，说明“核心循环 + 搜索适配器”已有实际集成路线。本项目首版仅做搜索，全文提取后置。[Parallel Pi Extension](https://docs.parallel.ai/integrations/pi-extension)

Ollama 官方有多轮和流式工具调用示例：发送工具声明，汇集 assistant 的 content/thinking/tool_calls，把工具返回以 tool 消息回填，再调用模型。第一版无需为此更换模型服务协议。[Ollama Tool calling](https://docs.ollama.com/capabilities/tool-calling)

边界：本日查阅的 Pi main 和在线 API 文档是可变来源，未固定版本进行本地复现；开发前固定协议样本和版本。未测搜索质量、价格、真实延迟，也未验证用户模型的工具能力。

## 4. 技术选型

| 路线 | 收益 | 成本 | 建议 |
|---|---|---|---|
| 小型 Python Harness | 复用 Qt 网络、SQLite、Ollama、取消语义；不用附带 Node/Bun | 自己实现有限循环、校验、运行记录 | **首版采用** |
| Pi RPC 子进程 | 复用成熟循环、会话、更多模型协议 | 安装/版本、扩展、RPC 生命周期、模型配置、双份会话适配 | 已评估，本版不采用 |
| 完整 Agent 框架 | 更丰富的编排 | 学习、依赖和打包面扩大 | 当前两个工具暂不引入 |

### 为何不采用 Pi RPC

已确认 Pi 仓库与其 agent-core、循环、Bash、截断、会话、RPC 各文件均真实存在且设计可参考（见第 3 节）。本版仍不采用，理由是收益与本项目当前需求不匹配：

- Pi 的三项主要收益是多模型提供商、扩展生态、会话分支，本项目作为个人工具**当前均不需要**，因此 Pi 实际能提供的只剩流式工具汇集与上下文管理。
- Pi 官方明确没有内置权限系统，其 Bash 默认以启动进程的身份直接运行。本项目第 6 节的安全设计（分类器、逐次确认、凭据隔离、来源校验）无论如何都要自己写，引入 Pi 并不减少这部分工作量。
- 采用 Pi 需要 Node ≥22.19 随应用分发，使已通过签名验收的冻结包退回待解决状态，并引入第二套会话存储与第二套凭据存储。
- 后续若 H0 证明本地模型不支持工具调用，采用 Pi 的方案沉淀为零；自研循环即便在中止判据的第三分支下，仍可替代原有单轮路径。

"后续要能拓展"与"需要 Pi"并非同一件事。本项目需要保留的扩展点是 `ToolRegistry`、`search_providers`、`OllamaAdapter` 与运行快照四个接口：加工具、加搜索 provider、加模型提供商、加能力模式都不需要改动循环。Pi 的扩展机制是分发型的（扩展包、skills、npm 生态），解决的是多人共享工作流的问题，与本项目无关。

### 重评 Pi 的触发条件

以下任一条命中即重新评估，不依赖主观判断：

- 需要接入第二个模型提供商，且不想为其单独编写 adapter；
- 需要 MCP；
- 需要子 agent 或并行工具执行；
- 需要会话分支或会话树；
- 自研循环的协议类缺陷修复连续两个工作包超过 30% 工作量。

开发顺序先 Exa 后 Parallel，因为 query/highlights 映射直接；不是搜索质量排名。最终两家共享接口，设置只选一个，不自动双发，不在失败时静默切换付费后端。

## 5. 最小架构与运行契约

```text
现有聊天窗（唯一入口，普通对话与任务共用）
       │ 请求快照、停止、工具确认
       ▼
 RunController ──────────────► SQLite 运行与步骤记录
       │                           ▲
       ▼                           │ 结构化事件
 RunWorker / RunLoop ─────────────┘
       ├─ OllamaAdapter ── 现有单轮 QtChatTransport
       └─ ToolRegistry（两项，顺序执行）
            ├─ BashTool ── 独立本地进程组
            └─ WebSearchTool ── Exa / Parallel HTTP
```

**只有一条执行路径。** 普通对话与 Action 是同一运行时在工具集为空时的单轮情形：`tools=[]`、`max_model_rounds=1`。`main.py` 中现有发送路径先以适配器接入，保留开关供对照，H6 验收后删除；不长期保留另一份网络发送、取消和持久化实现。

这样做的收益不止于去重：`ResultStatus` 的终态集合、回答版本选择、`regeneration` 与上下文组装都只有一份实现。保留「普通对话走旧路径」正是要避免的状态——两份上下文组装必然产生「新对话不接受旧任务文本」这类只在其中一份修掉的问题。

建议新增 `ai_desktop/harness/`：`types.py`、`loop.py`、`worker.py`、`controller.py`、`ollama.py`、`tools/bash.py`、`tools/search.py`、`search_providers.py`、`credentials.py`。不建设通用插件加载器。

运行快照固定 run_id、conversation_id、user_message_id、agent_id、origin（chat/action）、可选 action_id、模型配置、工作目录、允许工具、`bash_policy`、provider 和预算；不能引用可变全局配置，密钥不能进入可序列化快照。

步骤使用 step_id，工具使用本地 tool_call_id，并另存可选 provider_tool_call_id；每次模型请求再有 request_id。所有 UI/存储事件带 run_id/conversation_id；写入使用创建时归属，不能读取“当前对话”作为目标。

### Agent 与 Harness 的关系

Harness 是运行时，不是 Agent 类型。工具授权属于 run 快照，不属于 Agent 身份：同一个 Agent 在纯聊天时工具集为空，在任务模式下获得授权，二者走同一循环。三个轴相互独立，不能混谈：

| 轴 | 本版决定 |
|---|---|
| 执行路径 | 唯一循环。空工具集退化为单轮对话 |
| 能力授予 | 运行快照的 `allowed_tools`，由配置常量按 agent_id 映射，schema 预留后续字段化 |
| 身份呈现 | 不新增 Agent 入口，由 `general_assistant` 承载 |

**承载者。** `general_assistant` 获得工具授权，不新增第六个 Agent。这样避免新增 UI 概念、避免「选中专用 Agent 能否纯聊天」的取舍，且开放性任务用户本来就去通用助手。其余四个内置 Agent 与全部 Action 保持无工具。注意 `DEFAULT_AGENT_INDEX = 1` 指向 `code_expert`，因此默认 Agent 不获得工具；这是明确接受的取舍，扩大范围只是 `AGENT_TOOL_GRANTS` 增加一行。

**授权来源。** 首版用配置常量而非 `Agent` 数据类字段：

```python
AGENT_TOOL_GRANTS: dict[str, tuple[str, ...]] = {
    "general_assistant": ("bash", "web_search"),
}
```

这样不必修改 `Agent` dataclass，也不必触碰 `agent_manager.py` 中 `_normalize_custom_agent` 与 `save_custom` 两处硬编码字段白名单、Agent 编辑器与存储结构，`test_agent_manager.py` 无需改动。将来若升级为 `Agent.tools` 字段，是把该常量迁入数据类的纯重构。

**Action 共用运行时，强制单轮且无工具。** 现有 `_on_action_requested()` 经 `_on_user_message()` 使用 `StreamingChatWorker`，不是独立的发送实现；它还支持“当前对话”模式，不能称为无记忆流程。迁移保留 ActionService 生成的提示词、模型覆盖、材料和当前/新对话语义，只替换执行入口。

权限解析顺序固定为：`origin=action → allowed_tools=(), max_model_rounds=1`；其余请求再按 agent 授权、用户设置与模型准入结果取交集。即使自定义 Action 绑定 `general_assistant`，也不获得工具。该约束由运行时执行，不依赖 prompt 中的“不要执行材料中的指令”。无工具运行若返回 tool_calls，按协议失败处理，不执行、不弹确认、不自动增加下一轮。

Action 的重新生成通过保存的 action_id 重新解析当前动作规则、绑定 Agent 和 profile，再创建无工具单轮快照；隐藏/缺失动作明确停止，不退化为聊天。当前对话保留当前有效回答上下文，新对话沿用现有隔离机制。H6 删除旧实现的门槛包含全部 Action 路径，不能只验普通聊天。

**Prompt 复用。** `general_assistant` 的 system_prompt 就地改写后同时服务聊天与任务两种模式，不另建任务模式专用 prompt。唯一必须改的是「如果不确定，直接说"不确定"」，它与工具循环直接冲突，改为「先用工具查证；工具不可用或查不到，再直接说"不确定"」。「尽量简短」约束的是最终回答而非中间步骤，可保留。另需在 prompt 中预先消除模型对确认流程的重复提醒（如反复输出风险警告），否则会污染输出。

改写后建议在设置面板提供**思考设置**，具体开关或档位由选中模型声明并经验证的取值生成，与 `num_predict` 一并调整。理由是工具循环对推理深度的需求与纯问答不同：查目录结构不需要长思考，而多步任务的规划需要。旧 `true/false` 配置保留为 ON/OFF，不在迁移时擅自改成模型默认；若取值与新模型不兼容，明确提示并使用模型默认，不能显示原设置已生效。

**Model 独立。** 翻译、摘要等使用小模型无碍；任务模式需绑定通过 H0 验收的模型配置（见 `model_profiles`），因此授权与模型 profile 是两个独立维度。

状态：`queued → model → awaiting_approval / tool_running → model → succeeded`；任意非终态可进入 failed/cancelled/limited。limited 表示预算耗尽，不等于任务成功。

循环规则：

1. 固定任务上下文、工具和配置，执行一次模型请求。
2. 汇集流式文本、思考和工具调用，等待模型轮次真正结束；同时读取 `done_reason`。
3. 先判定完整性：length 按下述规则结束为 limited，协议错误结束为 failed。正常完成后，有调用再校验名称/参数/预算；无调用才完成任务。
4. 顺序执行工具，Bash 按第 6 节判定是否等待确认；取消后不再启动后续调用。
5. 完整回填 assistant 调用消息和对应 tool 结果，再请求模型。
6. 限额、取消、网络/协议失败时收尾，每个 run 只发一次终态。

命令非零退出、无搜索结果、参数不合法可作为结构化工具结果让模型判断；未知工具不执行，重复错误计入预算。连接失败/协议失败不能标为工具成功。

### 事件身份：StreamEvent 需携带 run_id 与 step_id

`StreamEvent` 当前只有 `request_id`，而 UI 侧靠**当前 worker**反查归属：

```python
def _on_stream_event(self, event: StreamEvent) -> None:
    worker = self._worker
    if worker is None or event.request_id != worker.request.request_id:
        return          # 不匹配即丢弃
```

这套在单轮下成立：一个 worker 对应一个 `request_id` 一次请求。但一次 run 最多 8 轮 = 8 个 `request_id`，两种朴素实现都不可用：

| 实现方式 | 失效原因 |
|---|---|
| 单 worker 内部循环多轮，但沿用初始 request 字段 | 第 2 轮起与第一轮 request_id 不匹配，被旧回调丢弃 |
| 每轮新建 worker，但沿用当前 worker 反查 | 旧回调可能被丢弃；若只放宽 run 判断，又会被追加到当前步骤 |

因此固定：

- `StreamEvent` 与单轮结果增加 run_id/conversation_id/step_id，保留 request_id；工具事件另带 tool_call_id。事件有 run 内递增 seq，用于重复事件与关闭步骤检查。
- 当前模型输出入口须同时匹配 `(run_id, step_id, request_id)`，且对应请求未关闭、run 未取消；只匹配 run_id 不够。
- 旧 step 的事件只能定位原 step 卡片/记录，不能追加到当前回答。单轮结束后依最终完整消息关闭 step；迟到增量不再修改它，完整消息保证已生成文本不会因迟到增量被丢弃而丢失。
- 工具事件按 `(run_id, step_id, tool_call_id)` 路由；模型单轮结束只发 step 终态，控制器只有在无调用、限额或取消等运行收尾时才发 run 终态。run 终态只校验 run 归属，不要求仍是当前模型步骤。
- 持久化按不可变事件身份和唯一序号记录，重复终态幂等处理；已取消 run 的迟到内容不修改最终答案，可保留不含正文的迟到事件诊断。
- 两处产出点都要改：`chat_client.py` 的 `ChatStream` 与 `qt_stream.py` 的 `QtChatTransport` 各自独立构造 `StreamEvent`。
- 事件不得携带可变引用；工具参数与摘录以字符串承载，避免跨轮次被就地修改（现有 `StreamEvent` 已是 frozen，沿用）。
- 该项是 H1 的前置结构。晚改会使 H1 的 worker、控制器与存储提交路径全部返工。

### 附件生命周期与图片预算

`StreamingChatWorker` 现有一次性持有：

```python
self._retained_attachments = storage.retain_attachment_paths(...)   # streaming_worker.py:40
```

图片在 `RequestContext` 中是**文件路径**，仅在发送前由 `_build_ollama_messages` 编码为 base64。因此：

- 采用单 worker 多轮后，附件引用活到 run 终态即自然正确；这正是选择该实现而非每轮新建 worker 的一个实质理由。
- 附件释放须与 run 终态对齐，不与单次请求对齐。`test_attachments.py` 现有 14 个测试均基于单次请求假设，需补多轮场景。
- **图片 token 必须计入上下文预算**。第 8 节的 `per_item` 公式当前只覆盖文本工具输出；一张截图 base64 后可达上千 token，任务中途再发图即可能撑破窗口。
- 发送前按固定估值折算图片占用；超限时**拒绝新图并提示用户**，不得静默发送。

### 纯对话是否记录 run 行

现有每轮对话只写两次 `save_message`（user 与 assistant）。引入 run/step 后纯对话是否也写行，需明确：

- 建议**一律写入**（`allowed_tools` 为空）。schema 5 只迁移一次，若先按"仅记录任务"设计，日后为普通对话补运行统计需再次迁移。
- 历史会话没有 run 行，属预期状态。UI 查不到 run 时不显示任务信息与目录，顶栏留空，**不得报错或显示占位符**。
- 相应地，`agent_runs` 语义为"所有运行"，`allowed_tools` 为空即纯对话；`agent_steps` 中纯对话只有 model 类型的 step。

### 截断轮次不得执行工具

现有解析层只看 message/done，不读 done_reason。新增 adapter 必须保存该字段与计数；length 表示生成没有正常完成，不能因已经出现一个调用对象就视为可执行。报文本身非法 JSON 或参数不合法则是协议/工具参数错误，不能混同为正常的 length。

首版固定：任务中 `done_reason == "length"` 时，本轮工具调用不执行、不进入确认；UI 保留部分文本并明确标记，run 结束为 limited。不要直接把截断 assistant 追加后再请求模型，也不假设模型会自行变短。普通对话/Action 同样保留部分文本并显示未正常完成，不把它作为成功答案自动激活。

不这样处理的后果是：半截命令进入第 6 节分类器，解析失败按 fail-closed 要求弹确认，用户看到一条**残缺命令的确认卡片**。这类噪音会训练出盲点点击习惯，使 fail-closed 本身失效——安全机制因误报率过高而被绕过，比没有更糟。

**预算与恢复分开。** 过小 num_predict 可能使模型还在思考就结束，不能据此断言不支持工具。单纯降低生成上限不会减少截断。H0 先测完整响应所需的生成分布，再确定组合参数；运行时首版不自动增大生成额度、不拼接半截工具调用、不重放之前成功的 Bash。用户调整参数后可明确重新执行，UI 告知这会产生新 run，可能重复已有副作用。

因此本节规则不能单独生效，须与第 8 节的取值一并确定：

- H0 测量总生成 token/时长分布，thinking token 仅在服务可靠提供时单独记录；缺失时记 unknown，不能用字符串长度充当精确 token。
- 首版倾向用较低思考档位配合较宽的 `num_predict`，以思考强度而非硬上限控制思考量。这也正是 `think` 结构化的实际价值所在。但该方案以档位**真正生效**为前提，须先经第 5 节的能力准入三段式验证；若实测发现档位被静默忽略，仅在布尔开关也被支持并验证时退回开关，否则使用模型默认重新测量预算。
- `num_predict` 的取值依据须记录在 H0 结论中，不得沿用未经测量的估计值。

### 预算耗尽：先收尾，再依据证据解释

不再等待连续两次 length 才停止。第一次 length 就收尾，保存 done_reason、eval_count、prompt_eval_count、请求选项与可获得的上下文估算。停止状态与原因归因分开，证据不足也能正常 limited，不能停在“思考”。

| 诊断 | 判据（须同时满足） | 处置 |
|---|---|---|
| `generation_limit_reached` | 服务 eval_count 的计数语义已经验证，且达到本次正数 num_predict | limited，提示检查生成额度/思考设置，不自动增加 |
| `probable_context_pressure` | 上下文估算逼近容量或有服务侧上下文旁证，但没有明确错误归因 | limited，显示为可能的上下文压力，建议缩小材料/摘录 |
| `budget_exhausted_unknown` | 仅知 length，指标不足或版本行为不明 | limited，只报告响应被截断，不制造原因 |

生成预算不是 thinking 专属，也不能假定放宽无害：它影响响应时长、内存/上下文压力和任务活动预算。因此没有证据时使用 unknown，不自动调整任何参数。

首版只允许一种有限的模型请求恢复：服务明确拒绝输入过长，且该失败步骤没有执行工具时，按完整协议块缩减可丢弃上下文，创建新 request_id 重试一次，仍计入轮次和时长。无法保留调用/结果配对、低于最小材料阈值或再次失败就结束。不得把重新请求模型变成重放旧工具。

**终止条件。** 任意一条满足即收尾：

- 第一次 length；
- 明确输入超长且一次有限恢复仍失败，或无法安全缩减完整协议块；
- 缩减后的工具结果已低于最小可用阈值（模型无法据此判断结果）；
- 轮数或时长已达第 8 节上限。

终止时给出与证据相符的说明；例如仅有 length 时显示“响应未完成，未执行本轮工具，可检查生成预算或材料长度”。已有成功步骤保留，不宣称回滚外部副作用。

### 三条数据路径：模型侧保真，持久化侧安全

> Model-facing data prioritizes semantic fidelity; persistence-facing data prioritizes safety and minimization.
>
> **Context reduction is an execution-budget operation, not a security transformation.**

工具输出有三条去向，语义各不相同，混用会造成实质错误：

```text
raw tool output (stdout/stderr/exit_code/duration)
   │
   ├─→ model context        原样，不脱敏、不因安全理由删改
   │      唯一允许的削减是 truncation/excerpt（执行预算）
   │
   ├─→ storage / logs      secret redaction + 最小化（安全）
   │
   └─→ UI 卡片             复用 model-facing 文本，不另做处理
```

**为什么模型侧必须保真。** 若对回填给模型的输出做脱敏，`cat .env` 会让模型看到 `API_KEY=***`，它据此得出的"未找到密钥"是错误结论。同理，第 5 节「UI 状态事件不进入模型消息」是同一原则的应用：持久化与呈现侧的加工不回灌到模型输入。

**两条变换不可互换。**

| | `redaction` | `truncation` / `excerpt` |
|---|---|---|
| 目的 | 安全与数据最小化 | 上下文预算 |
| 作用对象 | 存储、日志、导出 | 回填模型的 payload |
| 判据 | 内容特征（密钥形态） | 容量（token 预算） |
| 位置 | 写入前 | 组装请求前 |

禁止用 redaction 充当 context compression（会把可用信息删掉而预算未降），也禁止用 truncation 充当安全措施（落库内容仍是明文）。二者判据不同、可独立启用。

`redaction` 的最小实现：在写入 SQLite 与日志前，对疑似凭据的行做掩码（`API_KEY`、`Authorization: Bearer`、`*_SECRET_*`、`*_TOKEN*`、常见密钥前缀），保留可辨识前缀与长度以便排错。落库与日志之外的路径不回填模型，不受影响。

该原则的适用范围不限于此：UI 事件过滤、审计日志、工具结果截断、PII 掩码、遥测上报都遵循同一条分工。

### 能力准入三段式

**不要把模型或协议"接受某参数"视为该能力成立。** 本 Harness 对任何能力都走同一条路径：

```text
capability declaration  →  behavioral verification  →  admission
      模型声称支持什么           实际行为是否一致        是否准入本应用
```

第一段只读声明，代价极低但**不能作为结论**。第二段用可观测行为验证。第三段才决定该能力是否对本应用开放。跳掉第二段会得到一个"看起来成功、实际没生效"的实现，比明确不支持更难排查。

这不是 `think` 专属。以下每一项都适用，且各自的验证手段不同：

| 能力 | 声明来源 | 行为验证 | 涉及节 |
|---|---|---|---|
| 工具调用 | 请求被接受、返回 `tool_calls` | 多轮闭环成功率 | 第 9 节 H0 |
| 思考档位 | `/api/show` 的 `thinking.values` | `eval_count` / 延迟分布是否随档位变化 | 本节 |
| 视觉输入 | `/api/show` 的 `capabilities` 含 `vision` | 真实图片能否被正确描述 | 已实现（`service_checks`） |
| 结构化输出 | `format` 参数被接受 | 是否符合 schema 而非近似文本 | 本版不依赖 |

### `think` 须改为结构化语义，取值域由 capability discovery 决定

Ollama 官方定义 `think` 的合法取值为 `null`（用模型默认）、`false`、`true`，以及**模型自定义的档位字符串**，并要求使用 `/api/show` 返回的精确值：

```json
{ "thinking": { "values": ["low", "medium", "high"], "default": "medium" } }
```

官方同时明确：`values` 可只含布尔（仅开关）、可含模型自定义字符串；`values: [false]` 表示模型不支持 thinking；`thinking` 字段缺失表示模型可能按自身行为思考；**数值不被支持**。

两条直接后果：

- **不支持的档位名会静默回落模型默认，不报错。** 因此 UI 与校验必须使用 `thinking.values` 中的精确值，不能硬编码档位列表，也不能容忍拼写偏差——写错名字的表现是"设置成功但没生效"。
- **`thinking.values` 决定合法取值域，而非仅填充下拉框。** 若某模型返回 `[false]`，其合法 wire 选择只剩「模型默认 / 关闭」；返回 `["low","medium","high"]` 时不应出现「开启 / 关闭」，因为该模型可能没有纯开关语义。校验、UI 与序列化都按此域收窄；profile 另有不参与 wire 取值的「继承全局设置」。

因此 `think` 不再建模为 `bool`，采用设置模式加可选精确档位字符串，在 resolver 末端转换为 wire value：

```python
ThinkMode = INHERIT | MODEL_DEFAULT | OFF | ON | NAMED
# NAMED 携带 level: str，必须匹配 thinking.values 中的精确字符串
```

INHERIT 仅用于 profile，表示继续解析全局配置，不直接序列化为模型默认；这与现有 profile 的 `None` 继承语义一致。MODEL_DEFAULT 在解析完设置层之后才对应 wire value `None`（JSON null）。ON/OFF 分别仅在声明包含精确布尔 `true/false` 且通过验证时合法。NAMED 不硬编码 low/medium/high/max，以支持模型自定义档位。旧 profile 的 None 迁移为 INHERIT，新增显式 MODEL_DEFAULT 采用独立的存储标记，避免二者混淆。

`/api/show` 的请求基础设施已存在——`service_checks.py:231` 已调用并解析 `capabilities` 判 vision，但**未读取 `thinking` 对象**。扩展该解析器即可，不需新增网络路径。

改动落点：

| 位置 | 现状 | 需要 |
|---|---|---|
| `config.py` | `OLLAMA_THINK: bool = True` | 结构化设置，旧默认迁移为 ON；按模型校验并显示实际有效值 |
| `settings_manager.py` `_SETTING_MAP` | `bool` 转换器 | 专用归一化，区分继承全局与模型默认 |
| `settings_manager.py` `load()` | `val.lower() == "true"` | 兼容旧值 `"True"`/`"False"` 并接受档位 |
| `settings_manager.py` `apply()` | `conv(new_value)` | 按 capability 域校验，非法值提示并回落模型默认 |
| `ui/settings_dialog.py` | `("think", …, bool, True)` → `QCheckBox` | 新增枚举分支（现有分支只有 str/int/float/bool，无枚举类型） |
| `ui/model_profile_dialog.py:193-197` | `QComboBox` 硬编码三档 | 按选中模型的 `thinking.values` 动态生成条目 |
| `ui/model_profile_dialog.py:107` | `"思考开"` / `"思考关"` | 按实际档位显示 |
| `services/model_profiles.py` `validate_profile()` | 非 `bool` 抛错 | 接受模式与 NAMED 的精确字符串 |
| `services/model_profiles.py` `resolve()` | `bool(first("think", …))` | **去掉 `bool()`** |
| `llm/events.py` `RequestContext` | `think: bool` | 已解析的 `bool \| str \| None`，快照另存设置来源 |
| `llm/chat_client.py` | `bool \| None` | wire 值 `bool \| str \| None` |
| `llm/service_checks.py` | 只读 `capabilities` | 解析 `thinking.values` / `default` |

**必须删除 `bool(first("think", …))`。** 这是 silent corruption：`bool("low")` 为 `True`，档位会被静默压成开启且无任何报错。resolver 先解析继承关系，再按模型能力将模式转换成 `bool/str/None`，由 `_payload()` 原样序列化；不能把枚举对象或 INHERIT 直接发给服务。

`ui/settings_dialog.py` 的 `FIELDS` 类型声明直接决定控件：`bool` 渲染为 `QCheckBox`，现有分支无枚举类型，须新增一处 `key ==` 特判（参照 `pet_size` / `pet_source` 的既有写法）。profile 对话框已是 `QComboBox` + `currentData()`，架构上支持任意条目，但条目必须由该模型的 `thinking.values` 生成，不能沿用硬编码列表。

测试需同步核对：`test_model_profiles.py` 5 处、`test_request_results.py` 3 处、`test_streaming_worker.py` 2 处断言使用 `is False` 形式的布尔比较；`test_settings_manager.py` 与 `test_settings_dialog.py` 中涉及 `think` 的用例需覆盖 INHERIT 与 MODEL_DEFAULT 的区别、旧值兼容、自定义档位及非法档位的可见回落。

### Ollama 工具结果配对规则

当前 Ollama 官方源码已定义 `ToolCall.ID` 和 `Message.ToolCallID`；不能根据旧示例写死“不存在调用 ID”。实际服务是否返回/使用 ID 与其版本、模型协议有关，H0 保存版本和脱敏报文，形成 adapter 的配对能力配置。[官方 API 类型源码](https://github.com/ollama/ollama/blob/main/api/types.go)

- assistant 消息的 `tool_calls` 数组**保持模型返回的原始顺序，不重排、不去重**。
- 每个调用都有本地 tool_call_id（run/step/调用序号唯一）和可选 provider_tool_call_id；二者不能混用。服务端 ID 仅在本轮作用域内匹配，不假设跨轮全局唯一。
- 已验证支持 ID 且调用带 ID 时，回填 `role: tool`、tool_name 和对应服务端 tool_call_id；没有服务端 ID 的兼容路径按原始调用顺序回填 tool_name/content，不编造 wire ID。
- 同名工具不能合并；每个完整调用对应一条结果，包括拒绝和执行失败结果。本地身份负责执行去重，服务端身份负责协议配对，调用位置始终保存以供无 ID 回退。
- 流式 ID/index 只用于 adapter 已验证的汇集语义，不假定相同 index 必然是增量。无 ID/index 时按完成报文与调用顺序处理；无法无歧义重建就报协议错误，不能按相同参数去重猜测。
- 重复服务端 ID、缺失预期结果、错序或同轮混合 ID 都有协议样本验收；任何自动重试不得重放已执行工具。

`thinking` 是同一 message 对象的合法字段，回填在协议上成立；但官方未说明回填能提升效果，而它实打实占用上下文窗口。首版按第 8 节预算裁剪后的内容回填，是否回填完整 thinking 由 H0 实测决定，不预设收益。

## 6. 工具设计

### Bash

模型接口为 `bash(command, timeout_seconds?)`，cwd 由用户选定并固定，模型不通过参数自由换目录。命令的 cd、绝对路径、子进程仍能访问宿主，所以 **固定 cwd 不是文件系统沙箱**。

- 使用 `/bin/bash --noprofile --norc -c`，非交互、stdin 关闭，不加载用户 shell 脚本，不等待密码输入。
- 构建受控环境，不把搜索 API 密钥传给子进程；保留必要 HOME/TMPDIR/语言设置。GUI PATH 覆盖可发现的 Homebrew/常用工具路径并允许配置，不依赖 Finder 的 PATH。
- 只有结构化工具调用能触发执行；普通回答代码块、搜索网页里的命令不能被程序直接执行。
- 独立进程组、异步读取 stdout/stderr；取消/超时先终止，再短时间内强制清理同组子进程。自行脱离进程组的后台程序不在保证范围，首版不支持后台任务。
- 结果包含 exit_code、输出摘录、duration、truncated、error_type。持续输出也受内存/磁盘上限控制。
- 不自动重试 Bash：崩溃/断连后不能判断副作用是否发生，不能再执行一次假设未完成的命令。
- **不设独立的 bash 命令条数上限**，沿用第 8 节的工具调用总上限 16 次（搜索占用其中至多 6 次）。防止失控循环主要依靠任务活动时长与轮次上限；若 H1 实测出现模型反复调用同一读命令，可在后续补充次数闸，不在本版预设。

#### 危险判定与确认策略

保留 `readonly_auto / confirm_all`，但白名单不能成为跳过解析的入口。顺序固定为三步，首版不实现完整 Bash 解释器：

```text
1. 参数与语法检查   → 明确畸形输入返回工具错误；不能证明是简单语法则进入确认
2. 简单语法的检查   → 固定可执行文件、允许参数、规范路径、无副作用，全部满足才可自动执行
3. 其余有效命令     → 显示原命令及判定原因，等待执行/拒绝
```

运行快照的 `bash_policy` 取 `readonly_auto`（本版默认）或 `confirm_all`，两者均对用户暴露。默认 `readonly_auto`，**首次运行不弹窗选择**，避免一上来就要求用户做安全决策；设置面板提供开关，选择**按 workspace 记忆**，日常零摩擦，需要收紧的用户自行改为 `confirm_all`。取 `confirm_all` 时即使命中白名单也一律确认，便于在模型行为尚未实测阶段收紧。运行中策略不可变，改设置只影响下一次 run。

自动执行采用小型允许列表，只接受**单个命令、字面量参数与已知选项**。先检查整段输入，不按第一个命令名放行，也不用字符串切分冒充 Bash 解析。含管道、控制运算符、重定向、变量/命令/进程替换、glob、环境赋值或脚本结构时，首版不自动执行；不能准确判断引号/语法时同样不能自动执行。有效但超出简单语法的输入由 Bash 确认分支处理，明确缺引号等畸形输入返回工具错误，不展示残缺确认卡片。

允许列表初版刻意收窄，H2 按实际命令样本扩展，不一次覆盖整个 shell：

| 条目 | 条件 |
|---|---|
| `pwd` | 无额外参数，返回本次 workspace |
| `ls`、`cat`、`head`、`tail`、`wc` | 固定可执行文件、明确允许的选项；所有文件参数含选项值中的路径都须在 workspace 内；不接受 follow 模式、stdin 等待或未知参数 |
| `grep`、`rg`、`find`、`tree`、`diff` 等 | 首版确认；后续逐项证明参数/配置语义后加入自动执行，不只按“命令只读”放行 |

路径使用规范化后的 workspace 与 realpath，按路径组件判断包含关系，不能用字符串 startswith；符号链接指向外部、设备/特殊文件、无法解析的目标均进入确认。自动分支用经过验证的 argv 直接启动固定程序（shell=False），避免检查后再交给 Bash 做第二次展开；确认分支才执行原 Bash 字符串，记录实际使用的执行方式。

这会降低意外越界读取，但不会把工作目录变成沙箱，也不能保证消除外泄：目录内可能本来就有 .env，路径检查与执行之间也有竞态。保留模型侧原样输出的原则，不宣称“限定 workspace 就无需其他隐私边界”。联网仍按用户授予的搜索能力处理，其查询可能包含模型从任务材料中提取的内容。

分类矩阵：

| 类别 | 例子 | 判定 |
|---|---|---|
| 只读且在 workspace 内 | `ls`、`cat notes.txt` | 自动执行 |
| 读但越界 | `cat ~/.ssh/id_rsa`、`cat ../../secrets` | 确认 |
| 写/删/改 | `rm` `mv` `cp` `chmod` `dd of=` `ln` `truncate` `git checkout/clean/reset` `mkdir` `touch` | 确认 |
| 重定向写入 | `> f`、`>>`、`tee`、heredoc | 确认 |
| 网络 | `curl` `wget` `nc` `ssh` `scp` `rsync` | 确认 |
| 提权 | `sudo` `su` `doas` `launchctl` | 确认 |
| 内联代码解释器 | `python -c` `node -e` `ruby -e` `perl -e` | 确认（无法静态判定） |
| 动态执行 | `find -exec`、`xargs`、`make`、`npm run`、管道入 `sh`/`bash`/`python` | 确认 |
| 语法超出自动分支 | ` <(...)` 进程替换、其他无法证明为简单命令的语法 | 有效命令确认；已知畸形输入返回工具错误 |

网络命令归入确认还有产品理由：若 bash 可自由发起网络请求，`web_search` 工具就没有存在意义，且搜索请求会绕过第 8 节的次数预算、Keychain 凭据管理与来源校验（真实 URL 解析、拒绝虚构 ID、每次至多 5 个来源）。因此口径是外部网络一律走 `web_search`，bash 内的网络命令需确认。

判定器必须满足以下四条；首版通过收窄自动分支满足要求，不必递归解释全部 Bash 语法：

1. **全段检查在允许列表之前**：`ls && ...` 不因 ls 而自动放行。超出可证明的简单语法就确认，不尝试以正则遍历完整脚本。
2. **动态展开不能自动执行**：`$(...)`、反引号、进程替换等全部进入确认，无须为减少确认而开发递归 shell 执行分析器。
3. **参数和路径独立校验**：只读程序也可能有读取外部配置/执行子命令的选项，未知选项不自动执行；重定向不因命令名安全而放行。
4. **失败不能自动放行**：解析不明进入确认，已知畸形参数/语法返回工具错误；confirm_all 对有效命令一律确认。H2 前没有自动执行分支。

确认卡片显示准确命令与工作目录，并写明触发原因（检测到写入操作 / 越界读取 / 提权 / 网络请求 / 动态执行 / 超出自动判定范围），提供「执行 / 拒绝」。不写明原因会形成盲点点击，确认机制随之失效。确认绑定 run_id/step_id/tool_call_id/命令摘要；明确畸形输入只返回工具错误。

**确认等待 10 分钟后过期。** 过期按「拒绝」处理：向模型回填结构化的"用户未确认"结果，run 继续，模型可自行改用其他工具或直接作答。不得因等待过期而挂起整个 run——否则用户离开电脑后任务永久卡住，且会长期占用第 7 节「全应用同时只运行一个任务」的唯一名额。等待期间不计任务活动时长（见第 8 节预算表），与超时终止分开计数。

首版可执行命令范围的不确定性较大：模型实际会生成何种命令要到 H2 与功能评测之后才观察得到，因此分类器与白名单都需按第 9 节的验收清单覆盖绕过用例，而不是只验证正例。真正限制宿主访问仍需容器或沙箱；白名单、分类器与确认按钮都不提供隔离。

命令输出的落库与日志须经 secret redaction（见第 5 节「三条数据路径」），脱敏后的内容才写入 SQLite；**回填模型的副本保持原样**，不得因落库需要脱敏而连带删改模型输入。

### 联网搜索

模型接口 `web_search(query, objective?)`；provider 来自任务快照，模型不传密钥、Header 或供应商参数。

| provider | 请求映射 | 结果字段映射 |
|---|---|---|
| Exa | `/search`、query、type=auto、少量 numResults、contents.highlights=true | title/url/publishedDate/highlights |
| Parallel | `/v1/search`、可选 objective、search_queries=[query]；显式 basic（可选 turbo/fast/advanced）；max_chars_total 限摘录体积，来源数在本地裁剪 | title/url/publish_date/excerpts |

统一返回 sources（source_id/title/url/published_at/excerpt）、provider、检索时间、截断标记和错误类型。每次最多呈现 5 个来源，按预算裁剪，不抓整站，不开启深度研究服务。

回答中的来源 ID 解析为本次返回的真实链接；渲染层拒绝虚构 ID，URL scheme 仅限 http/https。网页摘录作为工具数据进入上下文，不变成系统指令。由网页内容诱导的 Bash 调用仍经过同一判定器；搜索请求本身按下条自动执行，二者不是同一个确认对象。

搜索请求一律自动执行，不设逐次确认，控量完全依靠硬上限：单 run 至多 6 次、配合工具调用总上限 16 次与任务活动时长。逐次确认在此不可用，也不构成费用保护。

缺密钥、401/403、额度不足、429、超时、服务错误、无结果分别反馈。暂时故障最多重试一次且计入预算；没有幂等保证时可能重复计费，不承诺重试免费。不自动把整个聊天/选区/本地文件发给搜索服务，只提交搜索参数。

设置增加 provider、密钥和连接验证。当前首批实现通过 PyObjC Security 直接访问 macOS Keychain，不提供环境变量回退；冻结包显式收集 Security。SQLite 仅存非敏感搜索参数，密钥不进入日志、导出或 Bash 环境。协议基础、两家适配器、测试入口与尚未接入工具循环的边界见 [Harness 协议与搜索设置](Harness协议与搜索设置-2026-10-04.md)。

## 7. 会话、上下文与存储

沿用备份机制，规划 schema 4→5：

- `agent_runs`：run_id、conversation_id、user_message_id、可选 generation_id、agent_id、origin/action_id、`bash_policy`、workspace、无密钥配置快照、状态、时间、有限错误代码。`mode` 不再需要区分 chat/task，但 origin 不能省略：Action 必须强制无工具。
- **纯对话同样写 run 行**（`allowed_tools` 为空）。schema 5 只迁移一次，若按"仅记录任务"设计，日后为普通对话补运行统计需再次迁移。历史会话没有 run 行属预期状态，UI 查不到时顶栏留空，不报错也不显示占位符。
- `agent_steps`：run_id、step_index、step_id、request_id、类型（model/tool）、本地 tool_call_id、可选 provider_tool_call_id、结构化消息/参数、有限结果、输出文件引用、状态、时间、payload_expires_at、payload_purged_at。元数据与可清空 payload 明确分字段，唯一约束防止重复记账。
- 会话持久化 workspace 与 `bash_policy` 或专用配置记录；旧会话不重写，无 workspace 的历史 run 视为纯对话。

具体 SQL 在 H0 后固定。存储由控制器串行提交，禁止跨线程复用 SQLite connection；大量输出写有限文件，步骤表只存摘录，不逐字符写库。

### 保留期与清理

现有 `storage.py` 已有 `collect_attachment_garbage()` 清理附件，不应声称不存在任何清理；但尚无 Harness 审计 payload 与输出文件的保留机制。以下规则在 H4 一并实现：

- **无内容的运行/步骤元数据随会话保留**：ID、时间、状态、工具名、退出码、调用数量、截断标志。8 个 model step + 最多 16 个 tool step + 1 个 run，约 25 行，不是 8×16。
- **审计 payload 统一保留 30 天**：命令和参数、模型中间消息/思考、工具摘录、HTTP 调试报文以及输出文件。工具内容在 model step 的副本也属于 payload，不因字段名不同而无限保留；从各步骤终态时间计算，interrupted 同样适用。
- 到期清空 SQLite payload 字段并标记 purged，删除关联输出，保留元数据；UI 显示“执行详情已过期”，历史加载不把占位文字当作工具结果重新注入模型。
- 删除会话立即级联清除运行、步骤与输出，不等 30 天。删除失败登记待清理，下次重试，不能只记录日志后永远遗留。
- 启动与历史详情读取前执行幂等清理；持续运行超过一天时在下一次空闲 run 边界检查，不引入独立定时任务。只处理已关闭且到期的步骤，不能删除活跃 run 使用的文件。
- 普通应用日志只写身份和有限状态码，不保存命令/正文；确需调试文本时放入受同一保留机制管理的脱敏审计 payload。不能先写原始临时文件，再只脱敏数据库副本。
- 用户主动保留的消息/最终回答遵循原聊天删除规则；30 天指新增审计内容，不声称清除最终回答中可能提及的内容，也不承诺 SQLite/WAL/外部备份的物理安全擦除。应用生成的含审计 payload 的备份也需记录保留规则，不能绕过清理无限保留。

**不按命令类别分级**，因为「命令风险」与「输出敏感性」是两个独立维度，按命令类型决定数据保留是错误抽象：

| 命令 | 类别判定的风险 | 实际输出敏感性 |
|---|---|---|
| `cat .env` | 只读 → 低风险 | **可能含凭据，最高** |
| `rm nonexistent` | 写入 → 高风险 | 仅"文件不存在"，几无信息 |

读取命令的输出敏感性取决于文件，写入命令的输出也可能仅是错误说明；仅按命令类别分级无法决定内容应保留多久。

首版统一 30 天的理由不是"30 天最优"，而是**当前没有数据支持任何更复杂的规则**。将来若要分级，判据应围绕内容敏感性、审计价值与存储成本三者的组合，而不是命令读写属性。

保留期与 secret protection 是两件事。30 天清理不能替代落库前脱敏；脱敏也不能延长审计 payload 的保留期。模型侧原样输出只保留在活跃运行内存与有预算的模型请求，不作为未脱敏的第二份持久化内容。

上下文原则：

1. 统一运行时，普通对话与 Action 是空工具集单轮情形；Action 强制无工具优先于 agent 授权。组装依据运行快照和 origin，保留 Action 当前/新对话语义，不读取可变 UI 状态。
2. 当前 run 保留 assistant 调用与全部 tool 结果的完整协议块，保留 Ollama 所需字段；UI 状态事件不进入模型消息。
3. 跨用户轮次主要保留用户输入、当前有效最终回答，以及必要的有限来源/产物摘要；旧原始工具输出用于审计，默认不整份再次注入。
4. 保留当前任务和最新完整工具块，按容量裁掉较早完整块；超预算明确结束，不破坏调用/结果配对。
5. 首版保守估算 token 并预留余量，不声称字符估算等于模型 tokenizer。若 H0 证明仍溢出，先缩小摘录/步数；自动 LLM 摘要压缩后置。
6. 新对话清空内存工具上下文；历史恢复只查看记录。重启后未完成 run 标为 interrupted，不自动续跑命令。
7. 切换回答版本只改变追问采用的最终答案，不重放工具。任务“重新执行”明确创建新 run，提示可能再次产生副作用，不套用普通“重新生成”语义。

全应用同时只运行一个任务。新建/切换对话和退出立即取消当前 UI 归属，异步收尾 HTTP 与进程；迟到事件只能更新原 run 记录，不能显示到新对话。每步启动前再核对取消令牌。

## 8. 用户体验与预算

入口：不新增 Agent 项。`general_assistant` 输入区提供当前会话的工具启用入口，默认关闭。用户明确选择 Bash / 搜索、工作目录和供应商后启用；历史目录只作为提示，不恢复授权。

2026-10-05 H5 决策更新：原方案倾向让通用助手直接具备授权、沿用历史目录。实际接入改为会话显式启用，以便用户在发送前决定本地命令与付费搜索的范围；切换会话或相关配置后重新启用。Agent 身份与运行授权仍分离，运行中的不可变快照不变，详见 [H5 显式工具任务入口](H5显式工具任务入口-2026-10-05.md)。

授权可见且可关闭。由于 `general_assistant` 从「不能执行任何命令」变为「可执行经确认的本地命令并可发起付费搜索」，输入框附近需常驻指示当前 agent 的授权范围与 workspace 目录；设置中可按 agent 关闭工具授权。关闭后该 agent 退化为纯对话，且不需等待模型重新判断即可立即生效——授权变更影响下一次 run，运行中的快照不变。

沿用现有聊天窗：顶栏显示任务与目录，默认卡片呈现“正在搜索…”/“正在执行…”/“完成，退出码 0”，展开看参数与输出；命令确认在同一卡片内完成，卡片写明触发原因，主按钮显示停止。结束显示答案和来源，失败保留步骤。执行中修改配置只影响下一次任务。宠物复用现有读取/工作/完成/失败状态。

**流式文本在卡片内实时显示。** 现有 `_on_stream_event` 已把 thinking 与 content 分开推送到 UI，管道存在，缺的是呈现决策。一轮之内的流式文本直接显示在卡片中，用户可看到"我先看看目录"这类过程，而不是干等一轮结束。

每轮模型使用稳定步骤卡片；收到工具调用后在后方追加独立确认/执行卡片，模型文字保留。本轮最终无工具调用时，模型卡片原位转为最终答案。切换时**已显示的文本保留在卡片内，不消失也不重排**——否则用户会以为输出被覆盖。thinking 内容折叠在可展开区域，默认不展开，避免与正文混淆。

以下为待实测的产品初值，不是已验证最优参数：

| 项目 | 建议初值 |
|---|---|
| 模型调用 | 最多 8 轮 |
| 工具调用 | 最多 16 次，其中搜索最多 6 次，错误也计入 |
| Bash 超时 | 默认 30 秒，可指定，最高 120 秒 |
| 搜索请求 | 连接 10 秒、响应等待 30 秒，支持主动取消 |
| 任务时长 | 活动 5 分钟；确认等待不计活动时长 |
| 确认等待 | 10 分钟后过期，按「拒绝」回填给模型，run 继续 |
| 审计 payload 与输出保留 | 统一 30 天；运行/步骤的无内容元数据随会话保留 |
| Secret redaction | 落库与日志前掩码疑似凭据；模型侧副本保持原样 |
| 模型侧工具摘录 | 单项先限 4 KiB，再按剩余上下文缩减 |
| 输出文件 | 单项 1 MiB，任务累计 5 MiB；超出后停止保留完整输出并标记 |
| UI 输出刷新 | 约 100–200ms 节流 |

上表必须区分两类上限，不能混读：

- **计数器上限**（模型调用 8 轮、工具调用 16 次、搜索 6 次）用于控量与防失控，与上下文容量无关。
- **注入量上限**（单项 4 KiB）受上下文容量约束，必须反推，不能当作每次调用的固定额度。

按源码默认 `num_ctx=8192` 估算：扣除 system prompt、用户输入、thinking 与最终回答约 2000 token，工具协议块实际可用约 6000 token。4 KiB 摘录按英文约 1000 token、中文约 1400–2700 token 估，单窗口**只能容纳约 3–5 个完整 4 KiB 结果**；16 次 × 4 KiB ≈ 16k–45k token，本身就是 8192 窗口的 2–5 倍。因此注入量按下式动态决定：

```text
available_tokens = num_ctx - estimated_existing_input_tokens - reserved_generation_tokens - safety_margin_tokens
per_item_tokens = max(0, available_tokens) / max(1, 本轮待回填结果条数)
per_item_bytes = min(4 KiB, conservative_tokens_to_bytes(per_item_tokens, 内容类型))
```

实现上先按字符数保守折算并预留余量，再逐轮重算；上述 token 数是量级估算，不等于模型 tokenizer 的精确结果。若 H0 证明 8192 下仍频繁溢出，优先缩小摘录与步数，其次为任务模式单独提高 `num_ctx`（见下）。

这里的削减是**执行预算**操作，不是安全变换：削减只依据容量判定，不依据内容，且仅作用于回填模型的 payload（见第 5 节「三条数据路径」）。削减后低于最小可用阈值即触发终止，不无限下调。

### 现有配置需要验证，不能从数值大小直接归因

源码默认仍是 num_ctx=8192、num_predict=20480、think=True。num_ctx 是上下文容量，num_predict 是生成上限，二者不是同一个计数器。当前官方 API 类型还定义了 truncate/shift，具体截断、滑动和错误行为依版本/runner 配置而变，因此不能声称 num_ctx 必然硬顶累计生成 token。[官方 API 类型源码](https://github.com/ollama/ollama/blob/main/api/types.go)

任务模式使用 H0 已验证的组合配置，不机械继承聊天默认，也不预设最小 16384。H0 可测试 8192/16384 等候选，结合模型限制、本机内存、延迟与多轮配对结果选值。普通聊天参数保持既有解析结果；新增 limited 呈现不意味着偷偷更改它们。

num_predict 与 think 一起测量，以验证总生成额度、思考和最终调用的关系，而非保证某个固定公式。另记录服务实际支持的 truncate/shift：若支持，可验证任务侧禁止隐式上下文删改的配置；不支持或声明缺失时先按 unknown 处理，不能在有工具配对的情况下依赖未验证的自动上下文滑动。

触及步数、时间和上下文限制显示已完成步骤与原因，不能无限继续。搜索次数用于控量，不保证固定金额，计费取决于模式和实际 API 行为。

## 9. 开发工作包

单人初估见下表合计（分支 A），不是发布日期。未包含新增云端模型提供商、真正 OS 沙箱或公开分发签名。缓冲不均摊：H2、H5 与 H6 涉及分类器、冻结包与签名，是本项目历史上最易超期的部分，各自多留 1 天。H0 为模型测试，其耗时取决于候选模型的实际行为，不计入工程估时。

| 工作包 | 预计 | 交付与验收 |
|---|---|---|
| H0 模型准入 | 2–3 天（原初估，不含在工程估时内） | 按 H0.1–H0.5 五阶段执行，每阶段产出 exit artifact；已有 9B 优先完成受限验证，27B 后续独立验证；原始观测全部留存；产出经验证的 `task_profile`；给出 admission result 与中止判据分支 |
| H1 统一运行循环（分支 A：named effort 已验证） | 6–7 天 | 类型、注册表、校验、顺序循环、身份、取消、预算；run/step/request 校验、分层终态、ID/无 ID 协议样本；附件 run 级持有；聊天和 Action 的强制无工具单轮路径；`think` 类型改造（12 处，含配置继承、capability-aware UI、旧值兼容）；length→limited、输入超长有限恢复；适配器和对照开关；两步模拟任务，终态只发一次 |
| H1 统一运行循环（分支 B：named effort 不可靠） | 5–6 天 | 同上，按已验证能力仅保留布尔开关或模型默认。capability discovery、capability-aware 合法取值域、resolver 修正、两处 UI、校验、摘要渲染与 service checks 均保留，12 个落点不减少 |
| H2 Bash 执行与判定器 | 3–4 天 | 先语法/参数检查再小型允许列表；简单 argv 与复杂 Bash 确认分支；realpath/选项/固定程序检查；确认原因与 10 分钟过期、进程组停止；中文路径、链接越界、非零退出、大输出、子进程验收；不实现完整 Bash 分析器 |
| H3 搜索适配 | 1–1.5 天 | Exa/Parallel HTTP、Keychain、配置、来源；模拟故障/取消，小额真实请求验证 |
| H4 会话与存储 | 2–3 天 | schema 5（含纯对话与 Action run 行）、备份升级、切换隔离、重启中断；落库前 redaction；无内容元数据与 30 天 payload 分字段、数据库/文件/诊断副本幂等清理；历史详情过期提示；打开历史不执行命令 |
| H5 Fluent 与打包 | 2–3 天 | 授权指示与 `bash_policy` 开关（按 workspace 记忆）、按 capability profile 生成的思考档位选择器、卡片内流式文本与状态切换、确认/停止、来源、重执行区分；`general_assistant` prompt 改写；完整回归、桌面与冻结包测试 |
| H6 删除旧发送路径 | 0.5–1 天 | 移除适配器后的旧发送实现与开关；验证普通聊天、四类 Action/自定义 Action、当前/新对话、Action 重新生成与模型覆盖；原正常响应行为保持，新增截断 limited 有明确回归 |

依赖为 H0→H1→H2/H3→H4→H5→H6。已完成独立 H0 探针与首个候选的受限验证。H1 协议与调度核心已接入普通聊天/Action 的空工具路径；动态 think 已完成 [类型迁移检查点](H1思考配置迁移-2026-10-04.md)；工作区/策略上下文、Bash 后端和线程安全确认协议已完成 [H2 核心检查点](H2命令执行边界-2026-10-05.md)及[桌面确认与执行反馈](H2命令确认与执行反馈-2026-10-05.md)。[H3 搜索执行与来源引用](H3搜索执行与来源引用-2026-10-05.md)和[H4 运行审计与历史恢复](H4运行审计与历史恢复-2026-10-05.md)已完成；[H5 显式工具任务入口](H5显式工具任务入口-2026-10-05.md)已完成会话授权、逐次准入检查、模型步骤卡片及真实 9B 闭环。[H6 统一运行时收尾](H6统一运行时收尾与桌面验收-2026-10-05.md)已删除名称导入别名、未使用的同步 chat/chat_stream 与旧字符串信号；控制器直接使用 RunWorker，动作重试正确恢复动作规则和 profile。1029 项回归、原生模拟交互与冻结候选检查通过；真实任务/权限/焦点/多屏体验仍按 H6 清单补验，不保留第二套执行路径。

H1 估时按 H0.2 分支，分支 B 仍包含能力发现、配置解析、两处 UI 与旧值兼容。按上表分支 A 算术合计为 **14.5–19.5 个工程工作日**；H2/H5/H6 各预留 1 天后为 17.5–22.5 天，另加 H0 2–3 天。总排期须待 H0 结论和固定接口后再评估，不把工程小计当成发布日期。

### H0 模型准入协议

H0 不是测试清单，而是**准入协议**：每个阶段都有明确输入、产出、通过条件与失败分支。未产出 exit artifact 不得进入下一阶段。

```text
H0.1 Capability Discovery
        ↓
H0.2 Behavioral Verification
        ↓
H0.3 Token / Latency Distribution
        ↓
H0.4 Budget Selection
        ↓
H0.5 Tool-use Admission
        ↓
TASK MODE ENABLED
```

| 阶段 | 核心问题 | 产出（exit artifact） | 失败处理 |
|---|---|---|---|
| H0.1 | 模型声明支持什么？ | capability profile | 收窄合法取值域 |
| H0.2 | 声明的能力真的生效吗？ | verified capability profile | named effort 未验证 → 已验证的布尔开关，或模型默认 |
| H0.3 | 实际 token / latency 怎么分布？ | 原始观测 + P50/P90/P95/max | 数据不足 → 不进入预算选择 |
| H0.4 | 什么预算不会制造 length 死锁？ | 已验证的 `task_profile` | 无安全配置 → 不准入 |
| H0.5 | 在正确预算下能否稳定使用工具？ | admission result | 失败 → 该模型不进入 task mode |

**只有 H0.5 允许得出「模型工具调用能力不够」的结论。** 在 H0.1–H0.4 走完之前出现的"没有 tool call"，只能记为**测试无效或未归因失败**，不得归档为模型能力结论。原因是失败可能来自任一前置环节——能力未生效、thinking 挤占生成预算、上下文被工具结果推高——这些都会表现为"不发出工具调用"，与能力不足在现象上无法区分。

#### H0.1 Capability Discovery

读取 `/api/show`，产出每个候选模型的 capability profile：`thinking.values`、`thinking.default`、`capabilities`（含 `vision`）。据此确定 `think` 的合法取值域（第 5 节）。

产出物须含模型名与 digest、Ollama 版本，不接受仅模型名。

#### H0.2 Behavioral Verification

验证 H0.1 声明的能力是否真的生效。**通过条件是「多次重复测试后，不同 effort 档位表现出可重复、可解释的行为差异」**，而不是"low 的 token 数必须小于 high"。

单次比较不可靠：随机波动可产生 `low=950` / `high=970`，据此无法判断。判据为：

- 各档位分别多次重复（同 seed、同 `num_predict`、同任务），记录分布；
- 需观察到**档位间的系统性趋势**（如随档位升高而 thinking token 单调上升，或至少存在超出组内波动的稳定偏移）；
- 分布无法与随机波动区分时判定为**未验证**，named effort 不作为可靠能力使用。

三组区分须贯穿全文，不得混用：

```text
API accepted ≠ supported
declared     ≠ verified
not disproven ≠ verified
```

不通过时收窄为已验证的布尔开关；布尔也未验证则使用模型默认，再进入 H0.3 测量。这是正常分支：capability discovery、capability-aware 合法取值域、resolver 修正、两处 UI、校验、摘要渲染与 service checks 全部保留。H1 的 12 个落点不因收窄取值域而减少。

#### H0.3 Token / Latency Distribution

测候选模型在三类任务下的实际占用：工具使用任务、规划任务、简单任务。

**须保留每次 run 的原始观测，不得只留最终统计量。** 报告只写 `P95 = 3820` 是不够的；Ollama 或 GGUF 换版本后行为变化时，无原始记录就无法追踪差异。每条记录含：

```text
model / model digest / Ollama version / task id / think value
num_ctx / num_predict / prompt_eval_count / eval_count / thinking tokens（若可得，否则 unknown）/ latency
请求与实际可验证的 truncate/shift / 预算估算与余量
done_reason / tool_calls
```

数据量不足（重复次数不足以给出分布）时不得进入 H0.4——用单次或两次的上界定预算等于把噪声写进配置。

#### H0.4 Budget Selection

产出**一组经验证的运行配置**，而非孤立的 `num_predict`：

```yaml
task_profile:
  think: <已验证档位/布尔，或 null 模型默认>
  num_predict: <由 H0.3 分布上界加余量确定>
  num_ctx: <由模型限制、本机资源与多轮样本实测选定>
  truncate: <支持时显式固定，不支持则记录 unknown>
  shift: <支持时显式固定，不支持则记录 unknown>
  tool_result_budget: <由上下文容量反推>
  max_rounds: 8
```

被验证的是这组参数**共同作用后的行为**，不是任何单一参数。单独确认 `num_predict=8192` 不足以保证组合安全。

通过条件：固定正常任务集在该组合下完成，没有因客户端预算/隐式配对破坏而失败；另有故意压低预算的边界样本验证 length→limited、零工具执行、保留部分文本。不能用边界样本的预期 limited 否决正常准入，也不能为提高成功率放开截断调用。

#### H0.5 Tool-use Admission

在 H0.4 的配置下评估工具使用能力：能否返回结构化 `tool_calls`、收到 tool 结果后能否继续调用或完成、改写后的 `general_assistant` 单一 prompt 能否驱动主动调用（而非直接回答"不确定"）。

**流式汇集的协议细节与模型能力无关**，可独立验证：有 `id`/`index` 按协议合并，无服务端 ID 按本地轮次与调用序号标识，不按相同参数去重吞掉合法重复调用，只在轮次完成后执行。该项不通过属于实现缺陷，归入 H1 修复范围，不构成模型能力结论。

#### 候选模型

原候选 `qwen3:8b`、`qwen2.5:7b-instruct`、`llama3.1:8b` 均未安装。2026-10-04 经用户确认，改为已有本地 `qwen3.5:9b-mlx` 与 `qwen3.8:27b-mlx`，优先 9B，从 H0.1 重新开始。9B 已受限通过，27B 当前仅 H0.1，不复用 9B 的分布或准入结果。此后切换模型仍须重新验证，不能通过无限换模型替代准入依据。

#### Task Mode 硬门

```text
task_mode_enabled = budget_verified AND tool_use_admitted
```

这是**硬门，不是警告**。H0 未完成时：普通对话与 Action 功能照常可用，**Task Mode 不开放**。不做"先让用户用、事后补验证"的过渡——那正是把能力声明当成能力成立的起点。

H0 的测试耗时与 H1 的工程估时分开计算，不合并。

### H0 中止判据

本地模型的工具调用可靠性不能预设。H0 必须给出结论分支，不能以"再换一个模型试试"无限延长：

- **全部通过**：按 H1–H6 计划推进。
- **仅 bash 单工具通过**：收窄首版范围，后置 web_search/H3；仍执行相同的命令判定规则，不能因移除搜索就宣称 Bash 没有外部访问能力。
- **全部不通过**：停止当前本地 Harness 路线，不继续 H1 工具循环与 H2–H6；H0 期间若已完成不依赖模型的 H1 聊天循环骨架，保留为独立重构成果，但不能宣称本版完成统一路径迁移。`general_assistant` 的 prompt 改写需回退，因为不再有工具可调用。改为增强现有确定性流程（`services/action_service.py` 已具备的固定动作能力），把"开放式任务"明确排除在本版本之外。云端模型提供商作为独立议题另行评估，需用户显式选择并单独授权计费，不并入本方案当前估时。

三种分支都要留下实测记录（模型 digest、Ollama 版本、失败类型），以便后续重评第 4 节的触发条件或改用其他模型协议时复用证据。

功能评测建议 10 个固定任务各运行 2 次：4 个单工具、4 个多步、2 个失败恢复。初始门槛为单工具成功率≥90%、多步≥80%，结果须有工具执行证据。这是小样本准入，不等于通用可靠性；确认拒绝、取消、隔离等确定性边界要求全部通过。记录总时间、步骤耗时、搜索次数和失败类型，不预设比 Pi 更快。

回归必须覆盖：

- 纯文本、工具后回答、工具后再调用、同轮多个工具顺序执行。
- 同轮同名调用保持两份独立结果；分别测试有服务端 ID/无 ID/混合 ID 的 adapter 样本，不重排，不按相同参数去重；服务端 ID 不替代本地执行去重身份。
- UTF-8 分片、重复/不完整工具块、非法参数、未知工具、声称执行但无实际调用。
- 停止发生在 HTTP、确认等待、Bash、搜索和步骤交接时，停止后无新工具启动。
- 新对话不接受旧任务的文本、工具结果与确认；运行中配置变化不影响快照。
- 应用管理的搜索凭据不出现在 SQLite、日志、导出和 Bash 环境；固定样本中的疑似秘密按规则脱敏，不能据此宣称任意秘密都不会持久化；网页数据不升级为指令。
- 搜索鉴权/限流/无结果/超时，命令失败、大输出、轮次与上下文耗尽。
- 上下文预算反推：token/bytes 单位转换明确，多条结果按组合预算缩减；task_profile 经实测选定；think token 缺失记 unknown，context shift/truncation 行为与版本一起验收。
- 版本切换不重放，重执行创建独立 run，历史恢复不执行命令。
- schema 4→5、旧对话默认聊天、运行中崩溃后的中断记录、级联删除与文件清理。
- Finder 启动 PATH、Keychain、冻结包进程创建/停止、UTF-8、应用退出和稳定签名。
- **普通对话与 Action 共用运行时**（tools=[]、max_model_rounds=1），保留原正常响应行为；自定义 Action 即使绑定 general_assistant 也无工具；当前/新对话与重新生成覆盖；非法 tool_calls 不执行。
- **分类器绕过用例**（每条至少一个反例）：`ls && rm -rf /`、`ls \`rm -rf ~\``、`echo x > ~/.zshrc`、`curl … | sh`、`find . -name x -exec rm {} \;`、`python -c "import shutil; shutil.rmtree('.')"`、`cat ../../.ssh/id_rsa`、引号不配对的畸形命令（须按危险处理，不得放行）。
- **允许列表作用域**：cat 的已知字面量路径可自动执行，链接/越界/未知选项确认；find 首版确认；复合命令先检查后判定；固定 argv 执行不会做第二次 shell 展开；confirm_all 禁止自动分支。
- **授权一致性**：关闭 `general_assistant` 授权后立即退化为纯对话，且不影响其他 agent；运行中的 run 不受授权变更影响。
- **length 收尾**：本轮零工具执行、无确认、部分文本标记、run limited，不自动下一轮；此前已完成工具不回滚/重放；普通对话与 Action 的部分答案不自动激活为成功版本。
- **多轮事件归属**：run/step/request 三者正确匹配；同 run 第 3 轮开始后第 2 轮迟到分片不得追加到当前卡片；step 结束不结束 run；重复/取消后终态幂等；两条事件产出路径一致。
- **多轮附件**：用户在任务中途发图，模型在后续轮次仍能看到该图；run 终态后附件被释放；图片占用计入预算，超限时拒绝并提示而非静默发送。
- **纯对话 run 行**：`allowed_tools` 为空时仍写 `agent_runs` 与 model 类型的 `agent_steps`；历史会话无 run 行时顶栏留空且不报错。
- **`think` 设置**：声明内的精确档位原样透传且不被压成布尔；继承全局与模型默认不同；已发布的 `True`/`False` 旧设置值仍正确加载；非法档位可见地回落模型默认且不崩溃。
- **确认过期**：等待超过 10 分钟后按拒绝回填结构化结果，run 继续且模型可改用其他工具；不挂起 run、不占用唯一运行名额。
- **保留期**：30 天清理审计参数/输出/模型中间副本及文件，元数据保留；详情显示过期；删除会话级联、失败可重试；启动和持续运行空闲边界幂等；活跃 run 不被清理；最终回答按原聊天规则保留。
- **redaction 与 truncation 不混用**：模型侧上下文原样含敏感内容，落库与日志已脱敏；上下文削减仅依据容量、不依据内容；redaction 不导致预算下降，truncation 不导致内容脱敏。
- **预算证据与恢复**：第一次 length 收尾；未知原因显示 unknown；仅明确输入超长可完整块缩减重试一次，新 request_id、不重放工具；达到最小材料阈值/轮次/时长即结束。
- **准入归因**：H0.1–H0.4 未走完时出现的「无 tool call」只能记为未归因失败，不得写入模型能力结论；Task Mode 在 `budget_verified AND tool_use_admitted` 之前不可用，H0 未过不得以「先试用」方式开放。
- **named effort 验证**：多次重复实验中若档位间差异无法与组内波动区分，判定未验证；仅在布尔支持也经验证时走 bool fallback，否则用模型默认重测；不接受单次比较或严格单调作为通过条件。

## 10. 下一次开发起点

H0 首个受限准入、H1 统一循环/思考迁移、H2 执行后端与 Fluent 确认卡片已完成检查点。[H3 搜索执行与来源引用](H3搜索执行与来源引用-2026-10-05.md)已接入唯一循环：两家真实密钥验证成功，本机 9B 分别通过 Parallel / Exa 搜索回填闭环。[H4 运行审计与历史恢复](H4运行审计与历史恢复-2026-10-05.md)已实现 schema 5、只读历史、30 天清理及答案版本来源恢复。[H5 显式任务入口](H5显式工具任务入口-2026-10-05.md)已完成代码与实测，[H6](H6统一运行时收尾与桌面验收-2026-10-05.md)兼容入口清理和动作重试回归也已完成，下一步真实桌面体验。通用助手显式启用后可以使用工具，普通聊天与 Action 保持无工具。继续采用实测的 9B/think=false/8192/1024 组合，保留 H0 模糊搜索失败作为路由风险样本。不安装 Pi 或 Node 依赖。

H0 必须先给出 `admission result` 与中止判据分支再进入依赖模型的开发：若全部候选模型未准入，就停在 H0，不投入 H2–H6。当前 9B 受限准入已满足进入 H1 的首个模型前提，27B 后续独立验证。事件身份、`tools=[]` 退化路径、旧路径适配器均不依赖第二个候选的结论。但**顺序不可颠倒的部分**是：`done_reason` 判定与预算反推的测试用例需要 H0.3 的真实分布作输入，用构造的假数据通过测试不等于验证了实际行为。

H1 按统一循环实施，不建独立的 Harness 专用循环：先让普通对话在 `tools=[]` 下走通同一路径并置于开关之下，工具循环在其上叠加。这样每个阶段都有一条可运行的路径，H6 删除旧实现时也不会出现功能真空。

当前交付包含受限模型验证、统一运行时、Bash/搜索执行及会话审计。H5 已开放受限 9B 的会话显式工具入口；H6 代码收尾、自动化和原生模拟交互验收已完成；真实桌面清单仍有待体验项，不宣布公开发布或完整桌面验收结束。

### 仍需实测确认的假设

以下事项未被当前 9B 小样本完全覆盖，后续实测仍可能调整方案：

- 当前单一验证 prompt 可驱动 9B 在明确任务下调用工具，但模糊搜索有路由失败；迁入应用的中文提示词、长任务与真实工具还需验证。
- 9B 已验证 think=false/8192/1024 的受控组合；其他配置和模型仍待测。truncate/shift 仍未知；16384 是候选而非强制下限，缺 thinking token 指标不能阻止按可靠总生成量做预算验证。
- 分类器的检出能力未经真实模型命令检验。首版按本节矩阵实现并覆盖绕过用例，但实际命令形态要到 H2 与功能评测之后才观察得到，分类器可能需要按实测结果调整类别或收紧条件。
- 9B 思考开关已验证，27B 的命名档位尚未验证。协议接受取值不等于生效：不支持的名字可能回落模型默认。其他模板是否忽略档位仍是假设，不能当作已实测案例。
- 30 天保留期未经真实使用验证，缺少存储占用与回溯需求的实测数据。首版统一保留是为了避免在没有数据时引入可能错误的分级；若后续数据显示不同类型输出体积差异显著，应围绕内容敏感性与审计价值重新设计判据。
- secret redaction 的识别规则为形态匹配，必然存在漏检。首版只做基础掩码，不追求完备；漏检内容会留在库内 30 天，因此该规则不能视为安全保证，只降低意外泄露的概率。
- 图片 token 折算缺少可靠依据。base64 长度与实际视觉 token 数没有固定换算关系，首版只能按保守估值计入预算；H2 应实测典型截图的占用量，若估值明显偏高会不必要地挤占工具结果空间。


## 2026-10-05 真实任务验收补充

[真实任务验收与稳定性修复](Harness真实任务验收与稳定性修复-2026-10-05.md)已记录首次发送的模型声明竞态和 Qt Worker 提前释放问题及修复。14 条源码原生真实 9B 工作流通过；重启诊断初次失败记录保留，修正脚本后使用合成历史单独通过。全量 1046 项回归通过。下一步补最新冻结应用的真实选区、权限、焦点、多屏与供应商搜索完整流程，不扩大 H0 模型准入范围。


## 2026-10-05 冻结应用验收补充

[冻结应用真实流程验收](Harness冻结应用真实流程验收-2026-10-05.md)已完成：新增隔离启动器和冻结程序诊断入口，最新 `.app` 原生窗口与真实 9B 的 16 条流程全部通过，重启使用同一冻结程序加载本轮实际保存的会话。全量 1060 passed，40 个相关冻结代码模块与源码一致。剩余选区/全局热键/权限/跨应用焦点/多屏/覆盖安装及供应商搜索完整应用流程仍按 H6 清单补验，不宣布完整桌面验收或公开发布。


## 2026-10-05 产品决策更新：工具跟随当前模型

工具模型不再固定为 9B；用户在同一对话切换顶栏模型时保留工具授权，取消旧检查/运行，下一次发送验证并使用新模型。新建或加载其他对话不继承授权。runtime 能力检查不等于完整行为验收，9B 历史 H0 证据仍保留；当前真实 27B Bash 闭环及冻结应用 9B→27B 同对话专项均已通过，全量 1069 项回归。详见 [工具授权与模型跟随修复](工具授权与模型跟随修复-2026-10-05.md)。该决策替代上方规划阶段的固定 9B 产品限制，命令确认、目录边界、工具循环和预算控制保持有效。
