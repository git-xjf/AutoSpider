# 技术方案落地对照

| 方案能力 | V0.1 实现位置 | 验证方式 |
|---|---|---|
| CrawlSpec / PageModel / CrawlPlan / Patch / Report | `packages/crawl_core/models.py` | Pydantic + `schemas/*.json` |
| Agent 状态机 | `packages/crawl_core/states.py` | 枚举终态和 repair counter |
| PageModel → CrawlPlan | `packages/crawl_core/planner.py` | `test_planner.py` |
| Hybrid deterministic compiler | `packages/compiler/playwright.py` | 相同输入任务 JSON 完全相同并固定执行模式 |
| HTTP/JSON CrawlPlan interpreter | `packages/executor/http.py` | HTML、JSON GET/POST 与翻页 E2E |
| Playwright CrawlPlan interpreter | `packages/executor/playwright.py` | Chromium 列表→详情 E2E |
| 持久任务、执行、日志与结果 | `packages/executor/repository.py` | adapter lifecycle tests |
| EasySpider 迁移兼容层 | `packages/compiler/versions/`、`services/easyspider_mcp/adapters/` | 0.3.5 compiler/HTTP/CLI 回归测试 |
| Compact HTTP overview + selector test + DOM/network probe | `services/easyspider_mcp/server.py`、`packages/inspector/` | 总览体积、分页 href、完整响应选择器计数、本地 HTTP/Chromium E2E |
| QA / Repair | `packages/validator/` | PASS、空字段、错误常量和最小 patch tests |
| 已观察路由分层字段门禁 | `packages/validator/coverage.py`、`ValidationReport.coverage_certificate` | 少数路由空正文误 PASS 回归、实际失败 URL 选择、本地 HTTP sample/full/记忆 E2E |
| Site Memory | `packages/site_memory/` | 低分拒绝、PASS 写入、fingerprint lookup test |
| 低 Token 记忆复用 | `packages/crawl_core/reuse.py`、`crawl_reuse_workflow` | 服务端重建 Spec/Plan、样本→全量 QA；本地 HTTP E2E |
| 低 Token 新站草稿 | `packages/crawl_core/drafts.py`、`crawl_draft_create`、`crawl_draft_execute` | primitive schema 无整套 typed definitions；sample→full→QA→memory 服务内执行 |
| 草稿持久化和顺序重试 | `CrawlDraftRepository`、`AUTOSPIDER_RUNTIME_ROOT/drafts/` | opaque ID、版本/hash、URL一致性、原子写、100KB结果上限、终态缓存及超时续同执行 |
| 爬虫轻量 profile / 工具按需发现 | `deerflow/agents/crawler-lead/config.yaml`、DeerFlow AgentConfig / Lead factory | 六个紧凑工具预绑定、普通工具 allowlist、短提示词、无 schema 搜索回执；原权限/Skill gates 保留，普通会话默认不变 |
| 草稿合同错误反馈 | `packages/crawl_core/drafts.py`、`crawl_draft_create` | 字段名与选择器关系、重复/未知字段分开反馈；非法参数有界返回且不持久化 |
| Windows MCP 路径转换 | `deer-flow/backend/packages/harness/deerflow/mcp/tools.py` | 远程URL和`//` XPath不触发UNC网络路径查询，真实本地文件保持路径转换 |
| DeerFlow Lead / Worker | `deerflow/agents/` | bootstrap 安装到 per-user Agent 目录 |
| 六个 Skills | `deerflow/skills/` | extension enable + per-agent allowlist |
| MCP 工具与权限边界 | `services/autospider_mcp/server.py`、`deerflow/config.autospider.patch.yaml` | Worker 无注册/启动工具 |
| 上游版本固定 | `UPSTREAM.lock` | bootstrap 发出 commit mismatch 警告 |

## 有意保留的边界

1. LLM provider 和密钥不写入仓库，沿用 DeerFlow 模型配置。
2. 默认执行不依赖 EasySpider 二进制；旧 checkout 只用于功能比对和旧任务迁移。
3. 真实站点 E2E 不纳入默认测试，避免 robots、站点改版和网络波动破坏确定性；默认只测本地 fixture。
4. HTML/浏览器执行器接受 XPath 和 CSS，JSON API 执行器接受受限 JSONPath；Selector 必须先用真实响应验证。
5. Sample task 把翻页限制为一页；默认 `boundary_spread` 在第一页候选范围选择确定性分散位置并限制输出条数，`prefix` 保留前 N 条行为。两者均不保证观察全部模板；full task 使用 CrawlSpec 上限。
6. 页数请求不猜记录数量：草稿设置 `page_limit`、保留 `item_limit=None`，执行器仍有既有 10,000 条安全上限。正文需求必须声明详情字段和观察到的详情链接。
7. 草稿结果缓存仅保证顺序重复不重新执行；并发同 ID 必须由编排层维持一个 execution owner。

## 未知站点的紧凑流程

已知且兼容的站点仍使用 `memory_lookup_site` → `crawl_reuse_workflow`。陌生静态站点可由 Lead 直接执行以下有界流程；复杂或客户端渲染页面才委派一次只读分析，不向主会话返回完整 DOM 或 typed 模型：

1. 直接调用预绑定的 `autospider_probe_http`、`autospider_probe_http_select`。HTTP 默认 `overview` 提供真实分页链接、短重复块和正文容器候选，不同时回传 DOM/可见文本/全文。选择器验证作用于完整响应，返回 counts 和有界预览；详情 URL 来自列表页证据，可直接验证正文容器。`mode="dom"` 及完整 `.tool-results` 回读不是日常步骤。
2. 验证结构后直接调用 `autospider_crawl_draft_create`、`autospider_crawl_draft_execute`。仅提交 URL、字段、列表/详情/分页选择器和限制，由服务构建严格模型与计划，返回草稿 ID。`detail_fields` 是唯一请求字段名列表，如 `["content"]`；对应绝对详情选择器写在 `field_selectors.content`。合同错误返回 `status=validation_error`、参数名、一次修正指引与示例，不创建草稿。
3. 执行草稿：sample → deterministic QA → full → final QA → validated memory，全程服务内部完成。小条数目标只跑一次全量并验证；页数目标保留样本先行。
4. 终态重复调用返回已保存结果；超时调用保留 phase 和 execution ID，下次从相同执行续行。FAIL 后新建最小修复草稿，不能重复未变草稿以期待不同结果。

旧 typed compiler、QA、memory-commit、浏览器和 lifecycle 工具不删除，但仅按具体高级修复需求逐个发现。爬虫 profile 的 `tool_search` 只返回推广回执，实际调用定义仍绑定；普通 profile 保留原 schema 返回。不能一次 `select:` 一批大定义；摘要只压缩历史，不能清除已经推广的常驻工具 schema。`tool_names` 同时过滤常驻普通工具与 late helpers，MCP 按需工具不因此删除；预绑定发生在原工具授权之后，活动 Skill 的模型与执行校验仍适用。

## 分页证据与模板

首先使用真实分页 href，不靠站点命名惯例猜测。404 是该候选无效而不是安全/网络全局阻断；最多再探测一个有页面证据的替代地址。私网/reserved-address PolicyViolation 则必须停止，不能切换传输方式绕过。

草稿与复用的执行阶段共用策略阻断分类：`PolicyViolation:` 前缀返回 blocked / policy_blocked，标记不可重试、需要用户确认且不需要重新分析。同站点范围内的兄弟子域名由服务许可；详情跳到无关站点则仍是授权问题，不启动选择器修复、不自动增加域名、不静默跳过记录；部分 PASS 或记录不能覆盖阻断终态，也不能提交完整验证记忆。

## 同站点与可信 Clash 兼容

`site_scope.py` 在管理员选用 `AUTOSPIDER_DOMAIN_SCOPE=site` 时以离线 PSL（含私有后缀）派生可注册域名范围，覆盖紧凑草稿、旧默认工作流复用、HTTP/浏览器探针和执行器默认白名单。自定义白名单不扩大；IP/本机名/未知后缀不进行父域推测。`allow_cross_domain_navigation` 无需开启，已有白名单与每次跳转校验继续生效。

`policies.py` 的 `AUTOSPIDER_FAKE_IP_MODE=verified_public` 只接受具有真实公网 DNS 答案的 Fake-IP 域名，固定 HTTPS DoH、A/AAAA/CNAME 校验、有限缓存与超时，失败关闭；其他私网与 Fake-IP 字面量不例外。严格模式兼容旧精确名单。集成模板明确启用可信本机代理模式，独立库默认严格；信任代理是管理员前提，不是模型从网页/会话中自主改变安全配置。

`{{page_number}}` 从 1 开始，默认 URL loop 在本页提取前打开模板。特殊首页采用 `pagination_after_extract=true`，保留首页并在提取后导航，末次不访问无用下一页；首页后是 `/2.html` 时用 `{{next_page_number}}`（当前 + 1）。既有首页后是 `index_1.shtml` 的提取后模板仍可使用 `{{page_number}}`，不改变旧默认语义。

## 累计 Token 防护

持久任务启动时记录 `owner_pid`，恢复阶段只中断没有存活 owner 的任务。Windows 使用只读进程查询，不用 `os.kill`；旧状态缺 owner 仍按孤儿恢复，PID 重用可能保守保留。该检查不替代跨进程 ID 分配锁或队列；同一草稿仅允许一个执行 owner。pytest 初始化模块级 MCP 仓库时使用临时运行/记忆目录，避免测试影响实际任务。

草稿自动 QA 读取最多 1,000 条；超过该范围返回明确的非完整验证标记，不提交整份工作流记忆。服务内 QA 不等同于模型阅读全量结果，也不能把有界前缀验证当成完整大文件验证。

保留 200,000-token per-run 上限。修复覆盖两轮审计发现的问题：早期 typed schema 常驻定义过大，以及后续紧凑工具条件下的通用系统提示词/无关工具/重复搜索回显/探针历史开销。`crawler_compact` 仅用于专用 Agent，不改变普通 DeerFlow；缓存 input 仍计入逻辑预算，不等同全部按未缓存价格收费。模型、站点复杂度和修复次数会影响实际消耗，不能承诺固定 Token 或耗时。

默认回归测试使用离线 fixture；上线验收另使用有调用次数、累计 Token 和时间上限的真实模型图运行。验收提示只包含用户 URL、页数和所需字段，不传预设选择器，也不由测试脚本代替模型调用爬虫工具，以覆盖模型分析 → MCP 草稿 → 执行 → QA → 记忆的完整链路。

## Repair 约束

RepairPatch 只作用于 CrawlPlan，不直接修改编译任务。当前确定性 patcher 支持字段 selector、fallback、item container、detail navigation、pagination 与 wait。`REQUEST_REANALYSIS` 必须重新交给 Web Analyst 获取结构证据。

## 分层验证与专利研究边界

验证器独立检查每个已观察路由结构的必填字段空值率，避免少数模板被全局均值掩盖；失败时给出字段、实际失败 URL 和预算声明。`coverage_certificate.scope=observed_records`，不代表未采样页面或整站完整性。补证预算目前由指令声明，尚无服务端自动补证消费接口；QA 模型回执总量则强制限制为 16,000 UTF-8 字节并声明详情省略。自动草稿只在完整 full QA 通过后提交记忆。

逐项对照、当前实施交底与实验边界见 [专利对照](patent/PATENT_COMPARISON_2026-10-07.md)、[交底草案](patent/TECHNICAL_DISCLOSURE_DRAFT.md) 和 [实验协议](patent/EXPERIMENT_PROTOCOL.md)。工程改进已验证，专利创造性仍需进一步检索和对比证据。
