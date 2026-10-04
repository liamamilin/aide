# 轻量 Agent Harness：调研、现状与开发方案

日期：2026-10-04。代码基线：`f3b3924`，分支 `codex/request-lifecycle`。状态：**方案完成，功能尚未实现**。

本轮仅调研官方资料、分析源码和制定方案。没有安装 Pi、调用付费搜索、执行模型生成的命令、改动业务代码或重建应用。上一轮 616 项测试和打包验收是既有基线，不代表 Harness 已经过验证。

## 1. 建议与首版范围

建议参考 Pi 的执行循环与事件设计，在现有 Python/Qt 项目中增加小型 Harness，继续使用 Ollama。模型只调用两个结构化工具：`bash` 和 `web_search`。搜索后端支持 Exa、Parallel，由用户选择其中一个，每次任务固定配置。

例如让任务助手“查看一个目录，分析文件，再联网查资料，最后给出结论”。闭环是：**提问 → 模型判断 → 工具调用 → 收到结果 → 再判断 → 回答或继续调用**。只把 Bash 输出拼进一次聊天，还不构成该能力。

首版包含单 Agent、顺序工具调用、流式回答、执行状态、命令确认、停止、有限预算和可查看的执行记录。不加入多 Agent、MCP、插件市场、计划树、自动记忆、定时任务、后台长任务或浏览器自动化；截图、OCR、朗读和宠物继续保留原有职责。

先验证协议和模型，再做完整界面与打包。当前本地模型的工具调用可靠性尚未实测。

## 2. 项目现状

| 位置 | 已有能力 | 所需变化 |
|---|---|---|
| `config.py` / `agent_manager.py` | Agent 是角色、提示词、模型配置绑定 | 区分普通对话与工具任务；旧 Agent 默认不拥有工具 |
| `services/model_profiles.py` | 参数校验、配置解析与回退 | 固定任务模型配置，独立检查工具能力 |
| `llm/events.py` | 不可变 RequestContext、request/conversation/agent 标识和终态 | 增加工具与步骤事件，区分单轮回复完成和整个任务完成 |
| `llm/chat_client.py` | Ollama `/api/chat` 与普通消息转换 | 发送 tools；保留 assistant tool_calls、thinking 和对应 tool 消息 |
| `llm/qt_stream.py` | UTF-8 NDJSON、连接/空闲超时、HTTP abort | 完整汇集工具调用；单轮 done 不直接终结整个任务 |
| `llm/streaming_worker.py` | 后台事件循环、取消记忆、附件保留/释放 | 新增多轮 HarnessWorker，工具不能阻塞 UI 和网络事件循环 |
| `main.py` | 发送/停止、会话切换、请求 ID 过滤、回答版本 | 接入小型任务控制器，避免把循环继续堆在 main.py |
| `utils/storage.py` | SQLite schema 4、消息、回答版本、迁移前备份 | 增加运行与步骤记录，工具轨迹不冒充回答版本 |
| `ui/chat_dialog.py` / `ui/fluent.py` | 默认 Fluent Light 对话、菜单、通知 | 用默认卡片呈现工具记录与命令确认 |
| 现有测试 | 请求快照、取消、协议、附件、配置、窗口 | 补多轮工具、进程取消、预算和任务切换测试 |

源码核对结论：

- `_payload()` 没有 tools；`_build_ollama_messages()` 目前只输出 role/content/images。解析层只消费 thinking/content，响应即使包含工具调用也没有执行链路。
- `_new_conversation()` 先停止 worker，再清空会话和消息；流式回调按 request_id 过滤。这是可以复用的隔离基础。
- `_context_messages()` 选择当前有效回答版本；发送前按 `OLLAMA_MAX_ROUNDS * 2` 截取消息。工具任务不能逐条照搬截断，否则会切断 assistant 调用与 tool 结果的配对。
- 源码默认 `num_ctx=8192`、`num_predict=20480`、最多 10 轮。这不是用户实时配置；工具结果和思考会占用上下文，需单独预算。
- `llm/service_checks.py` 当前探测图片能力；模型名称包含 Qwen 不代表已经通过工具调用验收。
- 截图/朗读的 subprocess 是应用固定命令，不能视作已存在供模型使用的 Bash 工具。

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
| 小型 Python Harness | 复用 Qt 网络、SQLite、Ollama、取消语义；不用附带 Node/Bun | 自己实现有限循环、校验、运行记录 | 首版采用 |
| Pi RPC 子进程 | 复用成熟循环、会话、更多模型协议 | 安装/版本、扩展、RPC 生命周期、模型配置、双份会话适配 | 后续备选 |
| 完整 Agent 框架 | 更丰富的编排 | 学习、依赖和打包面扩大 | 当前两个工具暂不引入 |

此选择针对现有架构，不意味着自研整体维护成本必定低于 Pi。以后若需要大量提供商、复杂扩展或会话分支，应重新评估 Pi RPC，避免不断复制其生态。

开发顺序先 Exa 后 Parallel，因为 query/highlights 映射直接；不是搜索质量排名。最终两家共享接口，设置只选一个，不自动双发，不在失败时静默切换付费后端。

## 5. 最小架构与运行契约

```text
现有聊天窗 / 任务入口
       │ 请求快照、停止、工具确认
       ▼
 HarnessController ──────────► SQLite 运行与步骤记录
       │                           ▲
       ▼                           │ 结构化事件
 HarnessWorker / RunLoop ───────────┘
       ├─ OllamaAdapter ── 现有单轮 QtChatTransport
       └─ ToolRegistry（两项，顺序执行）
            ├─ BashTool ── 独立本地进程组
            └─ WebSearchTool ── Exa / Parallel HTTP
```

建议新增 `ai_desktop/harness/`：`types.py`、`loop.py`、`worker.py`、`controller.py`、`ollama.py`、`tools/bash.py`、`tools/search.py`、`search_providers.py`、`credentials.py`。不建设通用插件加载器。

运行快照固定 run_id、conversation_id、user_message_id、agent_id、模型配置、工作目录、允许工具、provider 和预算；不能引用可变全局配置，密钥不能进入可序列化快照。

步骤使用 step_id，工具使用本地 tool_call_id，每次模型请求再有 request_id。所有 UI/存储事件带 run_id/conversation_id；写入使用创建时归属，不能读取“当前对话”作为目标。

状态：`queued → model → awaiting_approval / tool_running → model → succeeded`；任意非终态可进入 failed/cancelled/limited。limited 表示预算耗尽，不等于任务成功。

循环规则：

1. 固定任务上下文、工具和配置，执行一次模型请求。
2. 汇集流式文本、思考和工具调用，等待模型轮次真正结束。
3. 有调用则校验名称、参数和预算；无调用则完成任务。
4. 顺序执行工具，Bash 等待命令确认；取消后不再启动后续调用。
5. 完整回填 assistant 调用消息和对应 tool 结果，再请求模型。
6. 限额、取消、网络/协议失败时收尾，每个 run 只发一次终态。

命令非零退出、无搜索结果、参数不合法可作为结构化工具结果让模型判断；未知工具不执行，重复错误计入预算。连接失败/协议失败不能标为工具成功。

## 6. 工具设计

### Bash

模型接口为 `bash(command, timeout_seconds?)`，cwd 由用户选定并固定，模型不通过参数自由换目录。命令的 cd、绝对路径、子进程仍能访问宿主，所以 **固定 cwd 不是文件系统沙箱**。

- 使用 `/bin/bash --noprofile --norc -c`，非交互、stdin 关闭，不加载用户 shell 脚本，不等待密码输入。
- 构建受控环境，不把搜索 API 密钥传给子进程；保留必要 HOME/TMPDIR/语言设置。GUI PATH 覆盖可发现的 Homebrew/常用工具路径并允许配置，不依赖 Finder 的 PATH。
- 只有结构化工具调用能触发执行；普通回答代码块、搜索网页里的命令不能被程序直接执行。
- 首版每次调用使用默认 Fluent 内嵌卡片显示准确命令和目录，提供“执行 / 拒绝”。确认绑定 run_id/tool_call_id/命令摘要，过期确认失效，不用正则猜测安全命令自动放行。
- 独立进程组、异步读取 stdout/stderr；取消/超时先终止，再短时间内强制清理同组子进程。自行脱离进程组的后台程序不在保证范围，首版不支持后台任务。
- 结果包含 exit_code、输出摘录、duration、truncated、error_type。持续输出也受内存/磁盘上限控制。
- 不自动重试 Bash：崩溃/断连后不能判断副作用是否发生，不能再执行一次假设未完成的命令。

未来可考虑明确的“本次任务允许命令执行”。真正限制宿主访问应另做容器/沙箱，确认按钮和黑名单不替代隔离。

### 联网搜索

模型接口 `web_search(query, objective?)`；provider 来自任务快照，模型不传密钥、Header 或供应商参数。

| provider | 请求映射 | 结果字段映射 |
|---|---|---|
| Exa | `/search`、query、type=auto、少量 numResults、contents.highlights=true | title/url/publishedDate/highlights |
| Parallel | `/v1/search`、objective（缺省取 query）、search_queries=[query]；显式固定官方支持的交互模式 | title/url/publish_date/excerpts |

统一返回 sources（source_id/title/url/published_at/excerpt）、provider、检索时间、截断标记和错误类型。每次最多呈现 5 个来源，按预算裁剪，不抓整站，不开启深度研究服务。

回答中的来源 ID 解析为本次返回的真实链接；渲染层拒绝虚构 ID，URL scheme 仅限 http/https。网页摘录作为工具数据进入上下文，不变成系统指令，也不能绕过 Bash 确认。

缺密钥、401/403、额度不足、429、超时、服务错误、无结果分别反馈。暂时故障最多重试一次且计入预算；没有幂等保证时可能重复计费，不承诺重试免费。不自动把整个聊天/选区/本地文件发给搜索服务，只提交搜索参数。

设置增加 provider、密钥和连接验证。CredentialStore 建议使用 macOS Keychain，开发可兼容 EXA_API_KEY/PARALLEL_API_KEY；实现时验证所选 Keychain 库的冻结包依赖。SQLite 仅存 provider 与凭据引用，密钥不进入日志、导出或 Bash 环境。

## 7. 会话、上下文与存储

沿用备份机制，规划 schema 4→5：

- `agent_runs`：run_id、conversation_id、user_message_id、可选 generation_id、agent_id、mode、workspace、无密钥配置快照、状态、时间、错误。
- `agent_steps`：run_id、step_index、step_id、类型（model/tool）、tool_call_id、结构化消息/参数、有限结果、输出文件引用、状态与时间。唯一约束防止重复记账。
- 会话持久化 mode/workspace 或专用配置记录；旧会话默认 chat，旧消息和回答版本不重写。

具体 SQL 在 H0 后固定。删除会话/消息遵循现有级联语义，输出文件按会话和保留期清理。存储由控制器串行提交，禁止跨线程复用 SQLite connection；大量输出写有限文件，步骤表只存摘录，不逐字符写库。

上下文原则：

1. 普通对话保留旧路径；工具任务单独组装，不逐条按消息数量切片。
2. 当前 run 保留 assistant 调用与全部 tool 结果的完整协议块，保留 Ollama 所需字段；UI 状态事件不进入模型消息。
3. 跨用户轮次主要保留用户输入、当前有效最终回答，以及必要的有限来源/产物摘要；旧原始工具输出用于审计，默认不整份再次注入。
4. 保留当前任务和最新完整工具块，按容量裁掉较早完整块；超预算明确结束，不破坏调用/结果配对。
5. 首版保守估算 token 并预留余量，不声称字符估算等于模型 tokenizer。若 H0 证明仍溢出，先缩小摘录/步数；自动 LLM 摘要压缩后置。
6. 新对话清空内存工具上下文；历史恢复只查看记录。重启后未完成 run 标为 interrupted，不自动续跑命令。
7. 切换回答版本只改变追问采用的最终答案，不重放工具。任务“重新执行”明确创建新 run，提示可能再次产生副作用，不套用普通“重新生成”语义。

全应用同时只运行一个任务。新建/切换对话和退出立即取消当前 UI 归属，异步收尾 HTTP 与进程；迟到事件只能更新原 run 记录，不能显示到新对话。每步启动前再核对取消令牌。

## 8. 用户体验与预算

入口：现有 Agent 下拉框新增“任务助手”，普通 Agent 默认保持聊天。首次任务选择工作目录并配置搜索；聊天/任务模式切换进入独立会话，保留未发送草稿并给简短说明，不自动继承另一模式历史。历史加载以已存会话模式为准。

沿用现有聊天窗：顶栏显示任务与目录，默认卡片呈现“正在搜索…”/“正在执行…”/“完成，退出码 0”，展开看参数与输出；同一卡片内完成命令确认，主按钮显示停止。结束显示答案和来源，失败保留步骤。执行中修改配置只影响下一次任务。宠物复用现有读取/工作/完成/失败状态。

以下为待实测的产品初值，不是已验证最优参数：

| 项目 | 建议初值 |
|---|---|
| 模型调用 | 最多 8 轮 |
| 工具调用 | 最多 16 次，其中搜索最多 6 次，错误也计入 |
| Bash 超时 | 默认 30 秒，可指定，最高 120 秒 |
| 搜索请求 | 连接 10 秒、响应等待 30 秒，支持主动取消 |
| 任务时长 | 活动 5 分钟；确认等待不计活动时长，另设等待失效时间 |
| 模型侧工具摘录 | 单项先限 4 KiB，再按剩余上下文缩减 |
| 输出文件 | 单项 1 MiB，任务累计 5 MiB；超出后停止保留完整输出并标记 |
| UI 输出刷新 | 约 100–200ms 节流 |

触及步数、时间和上下文限制显示已完成步骤与原因，不能无限继续。搜索次数用于控量，不保证固定金额，计费取决于模式和实际 API 行为。

## 9. 开发工作包

单人初估 8–12 个工作日，约 20% 缓冲后 **10–15 个工作日**，不是发布日期。未包含新增云端模型提供商、真正 OS 沙箱或公开分发签名。

| 工作包 | 预计 | 交付与验收 |
|---|---|---|
| H0 协议/模型验证 | 1–2 天 | 脱敏 Ollama 样本；模拟单次/多次工具、thinking、畸形参数、取消；实际所选模型固定任务验证 |
| H1 最小内核 | 1–2 天 | 类型、注册表、校验、顺序循环、身份、取消、预算；两步模拟任务，终态只发一次 |
| H2 Bash | 1–2 天 | 命令确认、环境、目录、输出、进程组停止；中文路径、非零退出、大输出、子进程验收 |
| H3 搜索适配 | 1–1.5 天 | Exa/Parallel HTTP、Keychain、配置、来源；模拟故障/取消，小额真实请求验证 |
| H4 会话与存储 | 1–1.5 天 | schema 5、备份升级、运行记录、切换隔离、重启中断；打开历史不执行命令 |
| H5 Fluent 与打包 | 1–2 天 | 入口、默认卡片、确认/停止、来源、重执行区分；完整回归、桌面与冻结包测试 |

依赖为 H0→H1→H2/H3→H4→H5。本轮没有开始开发。

H0 的三个准入问题：

1. 所选模型能否返回结构化 tool_calls，收到 tool 结果后继续调用或完成？记录模型 digest 和 Ollama 版本，不能只记名称。
2. 流式调用如何汇集？有 ID/index 按协议合并，无服务端 ID 按本地轮次/调用序号标识，不按相同参数直接去重吞掉合法重复调用。只在轮次完成后执行。
3. 两工具任务是否可靠？不通过先推荐实测可用的本地模型，不能偷偷执行回答代码块，也不自动切收费云端。

功能评测建议 10 个固定任务各运行 2 次：4 个单工具、4 个多步、2 个失败恢复。初始门槛为单工具成功率≥90%、多步≥80%，结果须有工具执行证据。这是小样本准入，不等于通用可靠性；确认拒绝、取消、隔离等确定性边界要求全部通过。记录总时间、步骤耗时、搜索次数和失败类型，不预设比 Pi 更快。

回归必须覆盖：

- 纯文本、工具后回答、工具后再调用、同轮多个工具顺序执行。
- UTF-8 分片、重复/不完整工具块、非法参数、未知工具、声称执行但无实际调用。
- 停止发生在 HTTP、确认等待、Bash、搜索和步骤交接时，停止后无新工具启动。
- 新对话不接受旧任务的文本、工具结果与确认；运行中配置变化不影响快照。
- 密钥不出现在 SQLite、日志、导出和 Bash 环境；网页数据不升级为指令。
- 搜索鉴权/限流/无结果/超时，命令失败、大输出、轮次与上下文耗尽。
- 版本切换不重放，重执行创建独立 run，历史恢复不执行命令。
- schema 4→5、旧对话默认聊天、运行中崩溃后的中断记录、级联删除与文件清理。
- Finder 启动 PATH、Keychain、冻结包进程创建/停止、UTF-8、应用退出和稳定签名。

## 10. 下一次开发起点

先做 **H0 + H1**：模拟工具验证协议，再用选定的本地模型做有限真实验证。此时无需搜索密钥；H3 实际搜索验收才需要对应凭据。不先做大量 UI，不先安装框架依赖。

本轮交付为调研和可验收方案。后续完成模型/API/取消/会话/打包验证后，才能宣布应用已具备 Agent Harness 能力。
