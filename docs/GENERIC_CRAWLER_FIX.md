# 通用爬虫修复与真实模型验收（2026-10-04）

后续配置更新：同站点子域名与可信 Clash 的自动公网 DNS 核验已加入集成配置，详见 [README 的本机代理说明](../README.md)。下文的域名许可不足描述是当时验收状态，不是更新后的默认范围；旧失败 ID 仍不会自动重跑。

## 结论

重复超限主要是本地编排与工具上下文问题，不是本次 GLM API 身份验证失败。旧 ChinaDaily 会话的 9 次 Lead 调用累计 218,623 tokens，其中输入 217,316；还未执行爬虫就触发了 200,000 上限。

已完成通用代码、Agent 配置、SOUL 和 Skills 更新，保留原预算、三种执行模式、按需多智能体与网络防护。普通 DeerFlow 使用默认 standard profile，不受爬虫专用瘦身影响。

## 修复内容

- `crawler_compact` 使用独立短提示词和普通工具 allowlist。SOUL 从 8,863 字符缩短，保留任务保真、安全、执行所有权与错误处理。
- 六个紧凑 MCP 工具直接绑定，常规任务不再搜索工具；高级工具按需发现，搜索仅返回推广回执，实际调用 schema 仍绑定。原授权和活动 Skill 校验均保留。
- 默认 HTTP overview 不再重复回传 DOM、可见文本和正文；优先展示主新闻卡片、真实分页、代表文章与日期结构提示。排除导航/侧栏及祖孙重复块，不使用网站专用选择器。选择器计数仍基于完整响应；显式 DOM 模式保留。
- `detail_fields` 只接受唯一字段名，选择器放在同名 `field_selectors`。非法草稿返回参数、错误分类和一次修正指引，不保存草稿；字段类型说明列出合法值和省略时的默认值。类型错误不回显被拒绝的密钥输入。
- 执行策略阻断返回 `blocked / policy_blocked`，不可重试、不重新分析选择器、需要补充授权。覆盖样本/全量/续行/复用和旧失败缓存；不能把部分 PASS 当成完整成功或提交记忆。
- `pages_visited` 包括详情访问，不等同已完成列表页。超时继续原运行；终态失败不可通过同 ID 重试，授权变化需要新授权计划。

## 真实模型验收

使用已配置 GLM 和同一生产 `assemble_lead_agent` 图、真实 MCP；提示只提供 URL、页数和字段，没有传入预设选择器或由脚本代替模型调度。测试不经过前端 SSE/运行日志入库层，不宣称覆盖该层。

| 验收 | 结果 |
|---|---|
| 中国经济周刊银行栏目，空隔离记忆库，2 页 | 40 条标题/链接/发布时间/正文；QA PASS，覆盖率 100%，无空值、无重复 URL |
| 模型调用与用量 | 7 轮；input 63,838 / output 1,429 / total **65,267 tokens** |
| 端到端耗时 | **149.11 秒**，包括模型分析、样本、全量、QA 和记忆提交 |
| 参数纠错 | 一次字段类型合同错误，经结构反馈修正；无选择器/QA 修复 |
| 正文长度检查 | 232–2,717 字符；完整记录留在文件，不输入模型历史 |
| 旧 ChinaDaily 策略失败草稿续查 | 2 轮、13,983 tokens；正确停止，零阻断后修复/搜索，不重复爬取、不提交记忆 |

完整验收产物：`runtime/generic-verification/d9a2e1198283422e9fa2ca0e1dd69a8c/runs-root/runs/2/ceweekly-banking-2pages.json`。记忆库也在隔离目录内，未覆盖用户原工作流。

这是一例验收结果，不是所有网站的固定 Token/时间承诺。初始输入由旧 ChinaDaily 的 14,231 降到当前约 6,400 tokens；网站、对话轮数、模型和高级修复仍影响总消耗。缓存输入计入逻辑用量，但账单不一定按未缓存价格计算。

真实 API 调试与验收有成本：本次所有六次运行（含 Fake-IP 阻断、测试递归限制、跨域阻断、主卡片排序失败及最后两项成功验收）累计记录 **270,780 tokens**；上述 65,267 仅是最终完整验收那一次，不能代表本次开发过程的全部 API 用量。没有通过提高生产预算解决问题。

## ChinaDaily 的剩余授权边界

用户批准并经公开 DNS 核验后，仅 `cnews.chinadaily.com.cn` 被加入本机代理的精确 Fake-IP 信任名单；没有放开保留网段或其他私网。

两页列表有 56 条同域文章，以及 `jl.chinadaily.com.cn`（1）、`sx.chinadaily.com.cn`（2）、`cn.chinadaily.com.cn`（1）的文章。单域许可无法完整访问这些详情，通用修复现在会停下来询问授权，而不是继续试选择器或丢掉文章。**本次不宣称 ChinaDaily 两页已完整完成，也未新增这些域名的权限或 Fake-IP 信任。**

## 回归与使用

Backend 相关 26 个测试文件：662 passed / 1 skipped，含生产 factory 编译图、默认行为兼容、权限拒绝、活动 Skill 与预绑定工具测试。Ruff 检查通过。

AutoSpider 全部本地回归：281 passed，包含 HTTP GET/POST、JSON API、Chromium、草稿/QA/记忆、错误合同、策略阻断及概要体积测试。MCP 服务使用 `mytorch`，DeerFlow 后端继续使用其现有兼容虚拟环境。

Backend 默认全套和 strict blocking 套件都执行了修改前/后对比：Windows 上现有 `os.geteuid` 收集错误，以及三项 POSIX 文件权限测试、一项 Lark Unix helper 测试失败未变；没有将全 Backend 套件宣称为通过。

模板已同步到 default 和当前用户。更新后请在 `http://localhost:3001/workspace/agents` 选择 `crawler-lead` 新建对话；旧会话累积的历史和已推广工具不会自动清空。

可重复执行显式真实 API 验收：

```powershell
deer-flow/backend/.venv/Scripts/python.exe scripts/verify_crawler_agent.py --url https://www.ceweekly.cn/finance/banking/ --min-records 40 --isolated-memory
```

脚本限制 12 次模型调用、累计达到 100,000 后不开始下一轮、360 秒。单次返回可能跨过检查阈值；生产 Agent 原有 200,000 防护始终保留。默认 pytest 不调用真实模型或公开新闻站。
