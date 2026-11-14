# AutoSpider V0.1（原生混合执行层）

AutoSpider 是一个基于 DeerFlow 2.0、HTTP/JSON、Playwright 和 MCP 的多智能体网页数据采集系统。用户提供 URL 与自然语言需求，系统完成页面分析、传输策略选择、流程生成、小样本执行、质量验证、自动修复、完整采集和站点记忆。

迁移到另一台 Windows 电脑或交给 Work Buddy 接手，请先阅读 [项目交接与运行指南](markdown/AutoSpider_WorkBuddy_项目交接与运行指南.md)，其中包含角色、28 个 MCP 工具、六个 Skills、安装启动、数据路径和验收排错步骤。

宿舍与工位双机维护见 [GitHub 同步与修改记录指南](docs/GITHUB_WORKFLOW.md)。AutoSpider 和定制后的 `deer-flow/` 源码由同一仓库管理；密钥、实际本机配置、数据库及采集运行数据不随代码提交。安装脚本识别独立 DeerFlow checkout 的版本；单仓库内的框架目录不再误用父仓库提交号，原上游基线见 `UPSTREAM.lock` 和 `deer-flow/UPSTREAM_VENDOR.json`。

当前默认执行引擎已经是项目内自研的混合服务：静态页使用 `http_html`，公开稳定 JSON 接口使用 `http_api`，必须执行 JavaScript、登录态或交互的页面使用 `playwright`。不需要启动 EasySpider 桌面程序，也不依赖 `127.0.0.1:8074`。原 EasySpider 0.3.5 编译器及 CLI/HTTP 适配器暂时保留，仅用于回归比较和迁移旧任务。

## 架构

```text
用户 URL + 自然语言需求
            │
     DeerFlow Crawler Lead
        ┌───┴────────┐
  Web Analyst    QA / Repair
        └───┬────────┘
  Site Memory / compact selectors
            │
   AutoSpider MCP service
            │
   typed models → IR → compiler
            │
 HTTP HTML / JSON API / Playwright
            │
 sample → validate → repair → full → memory
```

三个智能体仍然保持原有职责：

- `crawler-lead`：唯一的执行编排者。
- `web-analyst`：只读页面分析、重复块和 Selector 发现。
- `qa-repair`：结果质量检查和最小 CrawlPlan 修复。

多智能体采用按需策略，而不是每个任务都同时调用三个模型实例：有兼容站点记忆时由 `crawler-lead` 走确定性快速路径；普通静态新站也可由主智能体完成，复杂或客户端渲染的网站才调用一次有界 `web-analyst` 分析；代码 QA 返回 `FAIL` 后才调用 `qa-repair`。更多智能体不自动意味着更快或更省 Token，重复上下文可能增加总消耗。爬虫专用 Agent 固定使用低消耗推理配置：支持关闭思考的模型关闭 Thinking；GLM-5.3 始终思考，因此映射为 `thinking.type=enabled`、`reasoning_effort=low`。模型切换脚本会为 GLM-5.3 的开启和关闭分支都生成合法参数，覆盖主/子 Agent、摘要和记忆调用。

执行阶段不会临时让大模型编写并运行 Python 爬虫。常规任务中，模型只提交紧凑的 URL、字段、选择器与分页证据；服务内部生成受类型约束的 `CrawlSpec`、`PageModel` 和 `CrawlPlan`，项目内固定的 Python 执行器按计划调用 HTTP 或 Playwright。`crawl_draft_execute` 一次完成样本、代码 QA、全量和验证后记忆提交，完整记录写入输出文件，模型上下文只接收短摘要。旧 typed 编译、QA 和 `crawl_execute_validated` 工具仍保留为高级调试/修复入口。

陌生网站先做紧凑 HTTP 总览和针对性选择器验证；需要正文时额外检查一个代表性详情页。原始 HTML 内容不足时，再打开一次浏览器并检查有限的 DOM 与 XHR/fetch 证据。优先级是 `http_api` → `http_html` → `playwright`，但只有公开、稳定、无需秘密签名且字段完整的接口才会选 `http_api`。通过 QA 的执行模式会随工作流写入站点记忆；下次兼容页面直接复用。

### 站点复用与 Token 预算

同域、同路径的网站只更换查询参数或请求已有字段的子集时，先用 `memory_lookup_site` 读取精简元数据，再调用一次 `crawl_reuse_workflow`。查询默认 `include_page_model=false`，仅给出元数据、字段、执行模式和工作流字段名；复用无需读取完整工作流或让模型重拼 JSON。服务内部完成样本 QA，PASS 后自动全量；目标不超过默认 5 条样本时，只执行一次全量并验证。结果只返回短摘要和输出路径。新路径、新字段或复用失败时才进入页面分析。

DeerFlow 模板开启 `tool_search`，`auto_promote_top_k=2`，并开启 `skills.deferred_discovery`。`crawler-lead/config.yaml` 单独启用 `prompt_profile: crawler_compact`：使用短提示词、精简普通工具 allowlist，并通过 `preload_tools` 直接绑定六个常用爬虫工具，不再为常规爬取搜索工具。普通 DeerFlow 对话仍使用原提示词和工具发现行为。预绑定工具仍经过权限与活动 Skill 校验，不能恢复被拒绝的工具。模型调用名带 `autospider_` 前缀。

陌生网站使用已经绑定的紧凑工具，避免重复发现和推广完整 typed schema：

1. 直接调用 `autospider_probe_http`、`autospider_probe_http_select`：默认 HTTP `overview` 仅返回短标题、正文容器候选、真实分页 href 和重复块等必要证据，不再同时重复返回 DOM、可见文本和文章全文；选择器工具仍在完整响应上计数，只返回少量样例。`mode="dom"` 保留为高级诊断入口，不要日常回读完整 DOM 或 `.tool-results`。
2. 直接调用 `autospider_crawl_draft_create`、`autospider_crawl_draft_execute`：验证选择器后生成草稿 ID，再由服务完成 sample → QA → full → QA → memory。输出只有 ID、验证摘要和文件路径。高级浏览器、typed 编译和修复工具保留按需 `tool_search`；爬虫 profile 的搜索结果只给推广回执，不再把完整 schema 重复写入历史，但工具实际调用 schema 仍正常绑定。

详情字段写法是 `detail_fields=["content"]`、`field_selectors={"content": "已验证的详情选择器", ...}`；`detail_fields` 只接受所请求字段的唯一名称，不接受 XPath/CSS。非法草稿返回有界的 `validation_error`、参数名和修正示例，且不会保存草稿。模型最多修正一次参数合同错误；页面选择器修复仍是独立的有界流程。

例如“爬取新闻正文，2 页”应设置 `page_limit=2`、详情字段和详情链接选择器，未指定记录数时省略 `item_limit`，不把每页条数臆测成 25。分页首先读取真实 href，404 只表示该候选地址无效；最多再检查一个有页面证据的候选，不连续猜 `index_*.html`。URL 模板的 `{{page_number}}` 从 1 开始；如果首页路径特殊、第二页是 `/2.html`，用 `pagination_after_extract=true` 和 `{{next_page_number}}`（当前页 + 1）模板。普通编号第一页仍使用默认“先按模板打开，再提取”；首页后接 `index_1.shtml` 的已验证旧模板可继续使用 `{{page_number}}` 的提取后导航。

同一草稿顺序重复执行会返回已保存结果，不重复采集或提交记忆。超时后继续调用相同 `draft_id`，服务续用保存的执行 ID 和阶段；不要创建新任务或并发执行同一草稿。失败后的修复生成新草稿。此机制不承诺并发同 ID 的一次性执行。

执行中的 `PolicyViolation` 不是选择器错误：草稿/复用统一返回 `status=blocked`、`retryable=false`、`requires_user_confirmation=true`，不继续探索或更换传输方式。部分记录不等于任务完成，也不写入已验证站点记忆。集成配置默认允许同一可注册站点的子域名详情；跳到无关站点仍需明确授权，不能偷偷删掉文章来宣称已采集完整页数。

财联社 10 条记录任务曾成功输出后触发 200,000-token 防护：224,098 tokens 中 220,307 是 input，3,791 是 output，主要负担来自多轮重复发送的上下文和工具定义，并非提取了 22 万字。缓存命中的 input 仍计入预算，但统计 Token 数不等于全部按未缓存价格计费；账单取决于提供商的缓存计价与实际 usage。保留爬虫 Agent 的 200,000-token 防护，通过精简入口和服务内复用减少输入。

未知站点曾有两类超限：早期完整 typed 定义推广合计 140,067 字符；后续 ChinaDaily 会话虽然只用了紧凑工具，仍因通用提示词、无关工具定义、搜索 schema 回显和探针重复内容，9 次 Lead 调用累计达到 218,623 tokens。通用轻量 profile 同时缩减这些重复输入，且为字段合同错误提供可执行反馈，不通过提高 20 万防护掩盖问题。Windows MCP 路径转换也已避免把网页 URL/XPath 中的 `//` 当作 UNC 网络路径。缓存 input 仍计入逻辑预算；实际用量受站点、模型和修复次数影响，不保证固定 Token 或耗时。更新后请新建 `crawler-lead` 对话，旧会话已积累的历史不会自动清空。

真实模型验收是显式付费调用，不属于默认测试。可运行 `deer-flow/backend/.venv/Scripts/python.exe scripts/verify_crawler_agent.py --url https://www.ceweekly.cn/finance/banking/ --min-records 40 --isolated-memory`。脚本使用同一生产 Lead factory、已配置模型和真实 MCP，只提供 URL/字段/页数，不预设选择器；空记忆和采集文件隔离在 `runtime/generic-verification/`，不覆盖用户工作流。上限为 12 次模型调用、累计 100,000 tokens 前停止下一轮、360 秒；单次模型响应可能越过检查阈值，服务原有 200,000-token 防护不变。

## 已实现能力

- `CrawlSpec`、`PageModel`、`CrawlPlan`、`RepairPatch` 与 `ValidationReport`
- 单页、列表、列表→详情、点击翻页、URL 翻页和无限滚动计划
- 静态 HTML GET/POST、公开 JSON API GET/POST、页码/游标翻页和请求节流
- Playwright 原生打开、点击、输入、下拉选择、键盘、悬停、等待、返回、滚动、截图和条件节点
- XPath/CSS/安全 JSONPath、相对 Selector、fallback Selector、文本/HTML/属性/URL 提取
- 同标签和新标签详情页、多个起始 URL、浏览器/代理/User-Agent/Locale/时区参数
- JSON、CSV、XLSX 输出，结果大小、记录数、页数和运行时间限制
- 后台执行、持久任务与执行实例、状态、日志、停止和结果读取
- DeerFlow Scheduled Tasks 定时采集与 durable MCP task 管理
- sample-first、确定性 QA、最小 RepairPatch、SQLite Site Memory
- SSRF/私网阻断、域名限制、robots、跨域导航限制
- DeerFlow MCP、三个 Agent、六个 Skills 和工具权限白名单

完整功能对齐状态见 [EasySpider 功能兼容矩阵](docs/EASYSPIDER_PARITY.md)。

## 安装与自检

项目固定使用 `mytorch` 环境：

```powershell
cd D:\VsCode\syslabe\AutoSpider
conda activate mytorch
python -m pip install -e ".[dev,xlsx]"
python -m playwright install chromium
autospider doctor
python scripts\smoke_test.py
python -m pytest
```

复制 `.env.example` 为 `.env`。默认关键配置为：

```dotenv
AUTOSPIDER_ENGINE=native
AUTOSPIDER_RUNTIME_ROOT=runtime/playwright
AUTOSPIDER_MCP_TRANSPORT=stdio
AUTOSPIDER_ALLOW_CUSTOM_CODE=0
```

`CUSTOM` JavaScript 默认关闭。只有在信任任务来源并确实需要脚本节点时，才设置 `AUTOSPIDER_ALLOW_CUSTOM_CODE=1`。

## 编译任务

```powershell
autospider compile tests\fixtures\crawl_spec.json tests\fixtures\page_model.json `
  --sample-size 3 --output-dir runtime\outputs\fixture
```

输出：

```text
runtime/outputs/fixture/crawl_plan.json
runtime/outputs/fixture/autospider_task.json
```

编译过程不调用大模型，结果可以审计和复现。

## 接入 DeerFlow

本仓库中的 DeerFlow 已经完成本机绑定。重新安装或为新用户安装时执行：

```powershell
conda run -n mytorch python scripts\bootstrap.py `
  --deerflow-repo D:\VsCode\syslabe\AutoSpider\deer-flow `
  --user-id default
```

脚本会：

1. 合并 `web-analyst` 与 `qa-repair` subagent；
2. 注册名为 `autospider` 的 MCP 服务；
3. 固定使用 `mytorch` 的 Python 解释器；
4. 安装 `crawler-lead` 和六个采集 Skills（含传输模式选择）；
5. 移除旧的 `easyspider` MCP 配置和旧 workflow Skill；
6. 在修改配置前生成 `*.autospider.bak`。

启动 DeerFlow 后访问 `http://localhost:3001/`。根地址不再展示 DeerFlow 宣传首页：未登录时进入登录/首次配置流程并保留返回地址，登录后直接进入 `crawler-lead` 新对话。输入目标 URL 和字段需求即可；不要从普通 `lead_agent` 新建爬虫任务。AutoSpider MCP 由 DeerFlow 自动启动，不需要另开桌面软件。

## 单独启动 MCP

stdio 模式：

```powershell
conda run -n mytorch python -m services.autospider_mcp.server
```

HTTP 调试模式：

```powershell
$env:AUTOSPIDER_MCP_TRANSPORT="streamable-http"
$env:AUTOSPIDER_MCP_HOST="127.0.0.1"
$env:AUTOSPIDER_MCP_PORT="8000"
conda run -n mytorch python -m services.autospider_mcp.server
```

常规入口为 `memory_lookup_site`、`crawl_reuse_workflow`、`probe_http`、`probe_http_select`、`crawl_draft_create` 和 `crawl_draft_execute`。MCP 继续保留浏览器/network、typed 编译、执行生命周期、QA 和修复工具作为高级入口。DeerFlow 中的完整工具名带 `autospider_` 前缀。

## 运行数据

### 可恢复工具错误

执行期的必填字段选择器未匹配现在返回 `status=needs_repair`、`code=selector_no_match`，携带失败字段、选择器、实际页面地址和页面作用域。该失败可能发生在 QA 报告生成之前；Lead 应定向验证选择器并创建新草稿，不能因为没有 `validation.status=FAIL` 就结束。终态执行不原地重试，未知内部错误和访问策略阻断不进入选择器修复。QA 角色与校验 Skill 已允许只读 HTTP 选择器探针。

`crawler_compact` 在可恢复失败后遇到过早结束时，每次运行最多补一次继续诊断机会；Token、循环、访问策略和模型提供方停止信号仍优先。旧的成功 HTTP 总览在后续模型请求中可缩短显示，最近四个工具结果及失败证据保持完整，原始会话和文件不被删除。此优化减少重复输入，不保证模型一定修好或固定账单降幅。更新代码后重启 Gateway，推荐新建爬虫会话。

可使用 `deer-flow/backend/.venv/Scripts/python.exe scripts/audit_crawler_runs.py` 只读审计历史运行用量并重放上下文裁剪；输出位于 `runtime/agent-diagnostics/2026-11-01/`，不调用模型 API。完整检查结论见 [本轮诊断](docs/AGENT_DIAGNOSIS_2026-11-01.md)。

MCP 边界将常见参数、选择器、网络及浏览器会话失败返回为结构化回执，而不是让智能体靠 `ToolException` 猜测。请检查 `status/code/parameter/field_errors/next_action`，不能因为协议 `isError=false` 就认为采集成功。HTTP HTML 选择器支持 XPath 1.0/标准 CSS，不支持 `re:test`、`fn:`、XPath 2.0 函数或变量；无效参数在 HTTP 请求前预检。单个字段失败保留其他有效字段证据，但返回 `validation_error`，不伪装成零匹配或完整 PASS。

智能体指令要求仅修正错误参数一次，语法错误不更换执行模式；可重试 GET 最多建议一次，POST/执行类操作不得盲重试。失效探针会话可重新打开一次，不能因此重启爬取执行。安全拦截仍为不可重试 `blocked`；未知内部问题返回非重试 `tool_error` 与 `incident_id`。`retry_limit` 是恢复指引，不是按会话计数的服务端硬预算。字段诊断/预览有 6,000 字符预算，完整源上的计数仍保留。

只读浏览器探针现在逐跳核验主页面 HTTP 重定向，并保留真实 URL/origin。为防止后续跳转绕过检查，Service Worker 禁用，可选 iframe/资源/XHR 的重定向采取保守阻止，可能影响部分动态页面证据，并返回警告；不应把本次探针修复说成所有执行器的完整网络加固。详见 [工具错误通用修复验收](docs/TOOL_ERROR_FIX.md)。

### 启动失败与本机代理

执行器会将启动前的 DNS、策略和浏览器异常写为 `failed`，并返回 `error`；使用 `execution_logs(execution_id, wait_seconds=30)` 等待终态，避免连续空轮询。`CrawlSpec.traversal.item_limit` 是全量记录上限，`sample_size` 仅用于样本；加载更多必须同时设置 `PageModel.pagination.type="click"` 和按钮选择器。

任务启动时保存 `owner_pid`；另一个 MCP 实例初始化不会把活进程的任务误判为重启中断。旧状态没有 owner 时仍按孤儿任务恢复；PID 重用可能保守保留运行状态。不要并发执行同一草稿；任务 ID 分配目前不是跨进程事务队列。测试默认使用临时运行/记忆目录，不接触用户的生产任务。

草稿自动 QA 目前最多读取 1,000 条记录。若实际输出更多，会返回 `validation_complete=false` 和 `needs_full_validation=true`，不自动提交整份工作流记忆；必须另行确定性验证完整文件，不能把前缀 PASS 描述为全量 PASS。

`http_api` 草稿若使用 POST 或额外 params/body，应使用原生 page/cursor 分页；不接受会丢失请求参数的 URL 模板组合。失败 QA 的样本字段也会截断返回，完整正文只留在结果文件中。

Clash/Mihomo 的 TUN/Fake-IP 模式可能把公共域名解析到 `198.18.0.0/15`，而单域名任务又会拒绝兄弟子域名的文章；这是两类独立限制。DeerFlow 集成模板现在使用下面的便利配置，适合管理员信任本机代理的环境：

```json
"AUTOSPIDER_DOMAIN_SCOPE": "site",
"AUTOSPIDER_FAKE_IP_MODE": "verified_public"
```

`site` 使用离线 Public Suffix List（含私有平台后缀）计算站点范围：`cnews.chinadaily.com.cn` 可访问 `jl/sx/cn.chinadaily.com.cn`，但不会误放行整个 `com.cn`，也不会将两个 `github.io` 租户当作同一站点。显式自定义白名单仍有效；陌生外部站点不自动放行。已记忆的旧单域默认工作流可在复用副本中按管理员启用的站点范围升级，不修改原记录。

`verified_public` 只处理 DNS 返回的 `198.18.0.0/15` Fake-IP。服务通过固定 HTTPS 公共 DNS 同时验证 A/AAAA 与 CNAME 链，确认真实地址均为公网，才接受可信本机代理的转发；核验有超时、响应体和缓存上限，失败关闭。旧精确域名名单不绕过此模式下的公网核验。真实私网、loopback、link-local、保留地址、Fake-IP 字面量、混合私网答案及核验失败仍拒绝，`block_private_networks` 不关闭。

独立服务默认仍为 `AUTOSPIDER_DOMAIN_SCOPE=host`、`AUTOSPIDER_FAKE_IP_MODE=strict`；strict 保留管理员精确 `AUTOSPIDER_TRUSTED_FAKE_IP_HOSTS` 的旧行为。公共 DNS 可验证域名，但无法证明恶意本机代理最终连接了哪里，因此便利模式必须信任本机 Clash；不可信代理应使用严格模式。公网 DNS 不可达时会阻断，不能保证任何站点都不再遇到访问限制。

新站通常无需逐个配置域名，也无需关闭 Clash；代理配置无需本工具修改。环境/工具代码更新后重启 DeerFlow，并新建草稿/对话。已经缓存的失败草稿仍是终态，不能复活旧 ID。详情站点属于同一站点只代表网络许可，不保证布局/选择器完全相同，仍需样本 QA。

本机验证与无模型重测命令见 [同站点 / Clash 修复验收记录](docs/PROXY_SITE_SCOPE_FIX.md)。

```text
runtime/playwright/
├── drafts/         # 草稿三模型、完整性 hash 与有界执行结果缓存
├── tasks/          # 确定性编译任务
├── executions/     # 不可变执行参数与状态
└── runs/{id}/
    ├── execution.log
    ├── result.json
    └── 可选 CSV/XLSX/截图
```

## 目录

- `packages/crawl_core`：协议模型、计划器、策略和状态机
- `packages/compiler/playwright.py`：CrawlPlan → 原生任务编译器
- `packages/executor`：HTTP/JSON/Playwright 混合解释器和持久任务仓库
- `packages/inspector`：浏览器网络探测、DOM 简化和 fingerprint
- `packages/validator`：确定性 QA 与 RepairPatch
- `packages/site_memory`：站点结构和 Selector 记忆
- `services/autospider_mcp`：公开 MCP 入口
- `services/easyspider_mcp`：旧模块路径及可选兼容适配器
- `deerflow`：Agent、Skills、MCP 与 subagent 配置模板
- `tests/websites`：离线可重复 E2E 页面

## 开源与上游边界

EasySpider 使用 AGPL-3.0，DeerFlow 使用 MIT，Playwright 使用 Apache-2.0。目前原生执行器是从 AutoSpider 的 CrawlPlan 需求独立实现的，没有复制 EasySpider ExecuteStage 代码。若未来直接合入或修改 EasySpider 源码，发布和网络服务需要遵守 AGPL-3.0；正式发布前应在项目根目录确定并加入与你采用方式相容的许可证。

本项目只应用于获得授权或允许访问的数据。不要绕过登录、验证码、付费墙、访问控制、站点限制或法律要求。
