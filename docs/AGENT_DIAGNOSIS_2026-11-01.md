# 智能体失败与 Token 消耗诊断

日期：2026-11-01。对象：AutoSpider 的执行服务、DeerFlow 的 `crawler_compact` 主控、QA 角色与已安装配置。

结论：存在具体的失败反馈和继续处理断点，不能将问题笼统归为模型能力不足。历史高消耗以多轮重复输入为主，另有较早版本的工具定义、正文和中间件开销。本轮已修改执行失败诊断、结构化错误识别、受控继续处理和旧成功探针的请求上下文显示，并同步了已安装的角色/Skill 能力。本轮没有调用付费模型，尚未测量修改后的真实模型成功率或账单变化。

## 1. 审计范围和证据

- 只读查询 `deer-flow/backend/.deer-flow/data/deerflow.db` 的运行元数据和选定会话检查点，共25条运行记录。当前可用的真实运行记录最新到2026-10-05，不是2026-11-01的新模型验收。
- 检查主控/QA 提示词、角色工具权限、活动 Skill 白名单、实际部署副本，以及 HTTP/Playwright 执行器、MCP 返回和模型中间件。
- 使用本地 HTTP 页面和脚本模型复现关键控制路径；另以当前部署网络配置对两个已有公开新闻列表做只读 HTTP 探测。
- 诊断数据见 [历史用量与重放](../runtime/agent-diagnostics/2026-11-01/history-audit.json)、[当前公开站探测](../runtime/agent-diagnostics/2026-11-01/public-probes.json)。没有导出整段用户提示、新闻正文、请求密钥或供应商完整载荷。

重放命令，不调用模型：

```powershell
deer-flow/backend/.venv/Scripts/python.exe scripts/audit_crawler_runs.py
```

## 2. 可确认的失败原因

### 2.1 必填字段未匹配，发生在 QA 之前

国际在线 `https://ge.cri.cn/policy` 的一次10条新闻任务，使用的发布时间选择器不能覆盖样本中的部分详情页。执行器抛出 `AdapterError: selector matched no values`，任务在提取阶段终止，尚未形成 QA 报告。

原服务对这类结果主要返回错误字符串及 `needs_reanalysis`，没有稳定的修复状态、失败字段和实际失败页面地址。主控的角色约定又将 QA/Repair 放在 FAIL 之后，容易漏掉“执行先失败，QA 根本无法生成”的情况。历史会话确实继续尝试了几次选择器分析，但最终提前给出未完成和选择方案，未在该轮重新执行修正规则；用户回复“方案A”后，下一轮才用修正规则完成10条并写入记忆。这是反馈和继续处理机制的缺口，并非完全没有尝试分析。

### 2.2 工具调用成功，不等于采集成功

MCP 可以成功返回一份包含 `status=blocked`、`needs_repair` 或嵌套 `validation.status=FAIL` 的数据。原通用工具元数据主要识别异常、错误文本和顶层 `error`，不能稳定识别这些业务状态；MCP 文本块及其 JSON 序列化形式也需要统一解包。结果是失败可能被元数据标记成成功，影响停滞检测和后续处理。

### 2.3 QA 角色的 HTTP 诊断能力缺失

原 QA 角色的工具列表没有 `autospider_probe_http_select`，校验 Skill 的允许工具列表也没有它。直接 HTTP 抽取失败时，该角色很难用同一传输方式对失败页做针对性选择器验证。仅修改提示词并不能补齐这项实际能力。

### 2.4 部分历史停止属于访问策略和模型服务问题

历史还出现过同站兄弟子域名未在 `allowed_domains` 内、代理 Fake-IP 被判为保留地址、robots 检查阻断，以及模型思考参数不兼容、余额不足和连接错误。它们不能一律当成选择器错误。

当前集成配置已经具有同站范围与 `verified_public` 公共 DNS 核验；本轮公开站探测沿用现有配置，没有修改信任名单或关闭校验。真实策略阻断、访问控制及未知内部故障仍停止选择器修复。历史 robots 结论也不能永久当作网站现状。

## 3. API 消耗为什么高

下表是数据库记录的累计用量，调用次数包括记录到该 run 的模型调用，不是网页请求次数：

| 历史运行 | 模型调用次数 | 输入 tokens | 输出 tokens | 总 tokens |
| --- | ---: | ---: | ---: | ---: |
| 国际在线10条，第一轮提前结束 | 11 | 128,828 | 2,149 | 130,977 |
| 同一会话回复“方案A”后完成 | 5 | 83,454 | 1,465 | 84,919 |
| ChinaDaily 曾触发预算限制的一轮 | 11 | 219,582 | 1,996 | 221,578 |
| 较早版本的财联社5条任务，后被中断 | 51 | 1,504,203 | 126,372 | 1,630,575 |

后两行属于历史版本表现，不能当作本轮修改后的实测结果。其中 ChinaDaily 的 Lead 部分为218,623 tokens，其余记录包括中间件调用；不要将 Lead 数和整轮总数混用。

主要原因是：每次模型调用会再次发送提示词、当前可见工具定义和保留历史。探针输出、选择器尝试、错误和方案说明越积越多，后续输入越大。一次只输出几百 tokens，也可能重新输入数千至上万 tokens。较早版本还存在完整 typed schema 推广、正文回读及较大的摘要/记忆开销；已有 compact profile 修过其中一部分，不能把旧问题全部重复算成本轮新发现。

国际在线这个两轮会话的检查点保留了完整用量：输入合计212,282 tokens，其中176,896是提供商报告的缓存读取，约83%。因此，累计 input 大不等于相同数量的未缓存输入费用。本地审计没有供应商扣费流水，不能据此判断人民币账单是否异常，也不能把缓存统计直接换算成固定节省金额。

在抽查的高消耗会话中没有看到 `task` 委派，记录的子智能体 tokens 为0；所查开销主要发生在主控和中间件。记录不完整时仍需检查实际回调覆盖，不能仅凭一个计数字段推广到所有任务。

## 4. 本轮完成的修改

| 修改 | 实际行为 | 关键位置 |
| --- | --- | --- |
| 必填字段提取异常携带诊断 | HTTP/Playwright 保留失败字段、选择器类型、页面作用域及可安全返回的实际 URL；不附带文章正文。含凭据或明显秘密查询键的 URL 不回传。 | [错误类型](../packages/crawl_core/errors.py)、[HTTP执行器](../packages/executor/http.py)、[浏览器执行器](../packages/executor/playwright.py) |
| 执行失败分类 | 必填字段未匹配返回 `needs_repair/selector_no_match`，明确可以在没有 QA 报告时诊断；原终态结果仍缓存，修正后使用新草稿。未知内部故障不再被统一标为需要选择器重分析。 | [服务](../services/easyspider_mcp/server.py)、[适配器](../services/easyspider_mcp/adapters/playwright.py) |
| 结构化结果语义 | 解包 JSON、MCP 文本块及序列化文本块，识别 QA FAIL、可修复执行失败和策略停止；避免协议成功遮蔽业务失败。 | [工具元数据](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/tool_result_meta.py) |
| 受控继续机会 | 仅 `crawler_compact` 在当前轮存在未解决可恢复失败、模型却提前结束时，最多补一次继续诊断机会；不自动生成选择器，不承诺修复成功。 | [CrawlerRecoveryMiddleware](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/crawler_recovery_middleware.py) |
| 旧成功探针上下文缩减 | 仅在模型请求副本中缩短较旧的成功 HTTP 总览，最近4个工具结果和失败证据不变；不删除原始会话，不截断保留的导航 URL。 | 同上 |
| 角色能力与部署同步 | QA 角色和校验 Skill 增加只读 HTTP 选择器探针，Lead 明确处理 QA 前失败；模板及两个已有用户部署副本同步。主控 SOUL 为3416字符，保持原3500上限。 | `deerflow/agents/`、`deerflow/skills/`、已安装副本 |

Gateway 与嵌入客户端均显式传入 profile，普通 `standard` Agent 不启用爬虫继续/上下文机制。新增中间件排在循环和 Token 停止守卫之前，反向 `after_model` 调度先处理停止信号；提供方安全/长度停止及已记录的运行停止原因也不被继续机制覆盖。

已安装配置修改前保存在 `deer-flow/config.yaml.before-recovery-20261101.bak`，用户角色/Skill 也有同日备份。结构化核对确认除 QA 的提示与工具列表外，其他配置项保持原语义；没有更改模型、API 地址、密钥或预算。

## 5. 效果证据及边界

历史国际在线会话的保留消息，在每次模型调用前做离线重放：累计消息正文字符量从202,625降至157,733，减少44,892字符，即22.16%；另一个 ChinaDaily 会话从210,884降至190,890，减少9.48%。这些数字只度量重发的消息内容字符，不包含检查点未保留的完整系统提示或工具 schema，不是新模型实测的 token、时延或金额降幅。

已有摘要丢弃的老调用不能从当前检查点完整重建。审计 JSON 对这种情况标记 `cache_scope_is_complete=false`，不把保留片段的缓存率当整轮缓存率。

本地 HTTP 回归已验证：必填字段选择器失败能指向真实失败详情页和字段；错误草稿不能自动写入记忆；重复执行仍复用失败结果；使用修正规则的新草稿通过后可以写入隔离记忆。脚本模型的真实 LangChain 图也验证了“提前结束 → 一次有界继续 → 执行修正草稿 → 返回结果”的同步和异步路径，以及 Token 停止优先。脚本模型按预设步骤行动，不能用来宣称真实大模型已学会修复。

2026-11-01 的两个公开列表探针按当前配置均成功取得页面：中国日报页面的紧凑概览未给出新闻重复块候选。这说明访问成功仍不足以确认条目/正文提取成功，后续需要选择器或浏览器证据；本轮没有据 HTTP 200 声称完成真实网站全量采集。

## 6. 测试结果

- AutoSpider 全套：**786 passed，1项既有 Pydantic Settings 警告**。见 [JUnit](../runtime/agent-diagnostics/2026-11-01/autospider-tests.xml) 与 [输出](../runtime/agent-diagnostics/2026-11-01/autospider-tests.txt)。
- DeerFlow 相关中间件、profile、模型解析与客户端：**486 passed，12 skipped**。见 [JUnit](../runtime/agent-diagnostics/2026-11-01/deerflow-targeted-tests.xml)。
- DeerFlow 全量离线入口在修改前、后都因 Windows 缺少 `os.geteuid` 而在 `test_acceptance_checks.py` 收集阶段失败，未宣称全套通过。见 [修改后输出](../runtime/agent-diagnostics/2026-11-01/deerflow-offline-after.txt)。
- 严格 blocking-I/O 套件修改前110通过、6失败；修改后112通过、4失败。保留失败为3个 POSIX 文件权限断言和1个 Lark CLI 的 WinError 193，均在修改前已失败；本轮未改对应组件。另2个修改前失败的超时测试在修改后通过。见 [输出](../runtime/agent-diagnostics/2026-11-01/deerflow-blocking-after.txt) 与 [JUnit](../runtime/agent-diagnostics/2026-11-01/deerflow-blocking-tests.xml)。

## 7. 使用和后续定位

检查时本机8001端口没有可连接的 Gateway，未停止或重启用户运行中的任务。源码与已安装配置已经更新，下一次启动 Gateway 后生效；建议新建 `crawler-lead` 会话，以免继续携带旧会话中的过时工具发现和错误结论。

后续真实验收应保持用户原字段与采集范围，记录最终 `execution_status`、`validation`、验证范围、修复过程、模型调用次数、input/cache/output 分项及实际账单。需要真实模型调用才能判断新版本成功率和总消耗，不能用本轮离线字符重放替代。对发生在2026-10-05之后的新失败，应取得对应 URL/会话记录再复现，避免套用历史原因。
