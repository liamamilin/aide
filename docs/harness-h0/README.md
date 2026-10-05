# H0 本地模型验证

验证入口为 `scripts/harness_h0.py`，依赖 Python 标准库，独立于 Qt、应用数据库和聊天发送路径。它只访问本机 Ollama；工具返回内存中的固定文件和搜索结果，不执行模型生成的命令，也不调用 Exa/Parallel。

2026-10-04 经用户确认，候选由尚未安装的 `qwen3:8b`、`qwen2.5:7b-instruct`、`llama3.1:8b` 改为已有的 `qwen3.5:9b-mlx` 与 `qwen3.8:27b-mlx`，优先验证 9B。所有候选重新从 H0.1 开始；27B 当前只完成能力发现，不继承 9B 的结果。

在项目根目录执行：

```bash
python3 scripts/harness_h0.py
python3 scripts/harness_h0.py --model qwen3.5:9b-mlx --evaluate
python3 scripts/harness_h0.py --model qwen3.5:9b-mlx --protocol-check
python3 scripts/harness_h0.py --model qwen3.5:9b-mlx --tool-recheck docs/harness-h0/2026-10-04-9b-v2/summary.json
```

默认只做能力发现；`--evaluate` 运行完整生成测试，`--protocol-check` 运行流式与上下文生成检查。每次写入新的时间戳目录；`--output` 可指定新目录，已有原始观测不得覆盖。传入云端模型、外部地址或重定向时拒绝访问。脚本不会下载模型。

`--tool-recheck` 也会运行模型：只复测完整十项任务各两次，前置 H0.2–H0.4 必须已有匹配版本/digest/提示词与预算证据。它记录来源报告和原始观测哈希，不声称重新测量前置阶段；缺少或变更证据则拒绝复用，重新从 H0.1 开始。

每个目录包含：

- `discovery.json`：服务版本、候选、digest、能力与精确思考取值。
- `observations.jsonl`：每次实际请求、响应、耗时与服务 token 计数；流式检查另保留原始 NDJSON 对象序列。缺失的 thinking token 指标记 unknown，字符数不冒充 token。
- `summary.json`：阶段结果、预算、逐任务轨迹和准入结果。模型准入表示可以进入 H1 开发，应用仍需后续运行时、真实执行器、权限、取消、存储和打包验收。

测试分阶段进行：思考开关三次重复；简单、规划、工具三类请求各至少五次；固定预算下正常闭环、单 token 截断、流式单次及重复调用、1/4 条各 4 KiB 的完整工具结果；最后十项工具任务各两次。工具结果来自模拟器，结果正确还必须具备对应调用证据。单工具成功率要求 ≥90%，多步 ≥80%，失败恢复全部通过。

仅完整结构化调用被模拟执行；首次 length 保留部分文本并结束；未知终止原因、畸形参数、重复服务 ID、无终态或不明确的流式参数增量不会自动执行。保留合法的同名重复调用，不按参数去重。流式探针只处理本次验证的完整调用帧，不假装覆盖所有提供商的 delta 协议。

首轮 `2026-10-04-9b` 使用较模糊的工具职责提示词，记录到查找本地文件替代网络搜索、跳过首次尝试等失败。`2026-10-04-9b-v2` 明确 Bash/搜索职责和请求顺序，保持相同十项任务、工具结果和评分规则重新验证；不能删除首轮失败记录。重复次数仍小，结果只适用于记录的模型 digest、服务版本、提示词与参数组合，不代表普遍可靠性。

两轮模糊搜索任务均未准入。`2026-10-04-search-diagnostic` 把两项任务明确为搜索网页，四次对照通过；`2026-10-04-9b-v3` 使用相同前置预算和模型，重新执行包含明确联网意图的完整 20 次任务，全部通过。应用工具仍未启用，模糊搜索风险保留。最终结论见 [H0 实测报告](../H0模型准入实测-2026-10-04.md)。

上下文滑动与隐式截断仍记 unknown。H1 必须主动控制完整调用/结果块与总输入预算，不依赖服务自动删历史。视觉输入、真实搜索质量、宿主命令行为和应用 UI 不在本探针的验证范围内。

协议依据：[Ollama Chat](https://docs.ollama.com/api/chat)、[工具调用](https://docs.ollama.com/capabilities/tool-calling)、[思考参数](https://docs.ollama.com/capabilities/thinking)。以本机实际报文为准，不把在线文档声明当作已经生效。
