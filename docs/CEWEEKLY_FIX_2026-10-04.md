# CEWeekly 新站 Token 超限修复验收

## 原会话事实

会话 `b8d32743-609d-4172-a541-9fddf3567f74` 的主 Agent 共 8 次模型调用，累计 input 237,476、output 1,901，合计 239,377。其中 cached input 157,696，Thinking 617，不是主要消耗。摘要另有 3,227 tokens；缓存逻辑用量不等于全部按未缓存价格收费。

一次批量 `tool_search` 返回 140,067 字符的工具定义。大型 typed schema 被推广后持续进入请求；完整 DOM 外置后又通过 read_file 读回。预算防护在真正执行任务前清掉工具调用，所以“准备开始”不等于已经采集完成。

`index_2.html` 是猜测路径，返回 404；真实第二页链接是 `https://www.ceweekly.cn/finance/banking//2.html`。页面链接保留上游双斜线。404 不是这次 Token 超限的直接原因。

DeerFlow Windows 路径转换曾把 HTTPS 链接的一部分当 UNC 网络共享路径解析，使 HTTP 已返回后工具仍停顿数分钟。新实现先识别完整 URI、拒绝远程/UNC 路径，并在文件系统查询前检查本地目录边界。

## 修复

- 默认 HTTP 探针返回紧凑页面总览、真实分页链接和重复块证据；选择器工具检查完整响应，但只返回计数与有限样本。
- 新站使用 `crawl_draft_create` / `crawl_draft_execute`：服务内构造严格模型、编译、样本 QA、正式采集、最终 QA 和站点记忆。高级 typed 工具保留，不再作为常规发现路径。
- 三种模式继续保留。POST/带参数 API 使用原生 page/cursor 分页，草稿不接受会丢失请求参数的 URL 模板组合。
- 失败 QA 中的长正文证据也会截断；完整结果只保留在文件。超过 1,000 条时显式标记有界前缀 QA，禁止自动当全量验证提交记忆。
- 同草稿终态重复返回缓存；超时续用原 execution ID、样本大小和阶段。
- 分页增加 `next_page_number`，保留原 `page_number` 语义；特殊首页提取后访问下一页，末页不多抓一页。
- 并行验收额外复现并修复另一 MCP 实例误中断活任务的问题：启动记录 owner_pid，恢复前检查 owner 存活。测试运行/记忆目录隔离。
- Agent、Skills、工作流和 web-analyst 工具权限已同步到 default 和实际用户；20 万 Token 防护保留。

## 真实采集验收

通过实际配置的 mytorch stdio MCP 调用，无大模型 API 请求。

- URL：`https://www.ceweekly.cn/finance/banking/`
- draft：`acafcf836862421bb0d72e474eea2a44`
- 样本 execution：21，5 条，QA PASS。
- 全量 task：20；execution：22；workflow：6。
- 模式：http_html。2 个列表页、40 个详情页，40 条唯一记录。
- 标题、链接、绝对发布时间取自列表，完整正文来自详情 `//div[@class='page_content']`，四字段覆盖率 100%，QA PASS，score 1.0。
- 输出：`runtime/playwright/runs/22/ceweekly-banking-pages2.json`。
- 包括探针、样本和全量的直接执行链路约 109.95 秒。重复同 draft 验证返回同 execution，未再次采集。

该耗时不包含智能体的大模型分析，不能等同于下一次聊天的总耗时/Token。四个紧凑入口的工具定义和说明合计约 6,592 字符；这是定义尺寸对比，不是整次聊天用量承诺。

## 回归与运行

AutoSpider 全套使用 mytorch，246 项通过（1 个已存在的 FastMCP/Pydantic 提示）；受影响 DeerFlow MCP 六文件回归使用其 backend .venv，172 项通过、1 项跳过。DeerFlow 默认离线全套仍有已存在的 Windows `os.geteuid` 收集错误；blocking-I/O 112 项通过、4 项原有 Windows chmod/Unix 假执行文件问题未修改，本次无新增失败。具体结果在 backend/.tmp/*-after-ceweekly.xml。

后端健康检查正常。`http://localhost:3001/` 未登录时 307 到登录页，返回目标为 crawler-lead 新对话。应新建 crawler-lead 对话复测；旧会话已有的工具定义和大历史不会被代码修复自动清空。

局限：PID 重用可能保守保留孤儿任务；任务 ID 分配仍不是跨进程事务队列，同一 draft 不支持并发提交。不会全局关闭私网保护；本机 Fake-IP 信任仅单独加入已核验的 `www.ceweekly.cn`。
