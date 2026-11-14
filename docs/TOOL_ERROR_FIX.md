# MCP 工具错误通用修复

日期：2026-10-05。环境：Windows / `mytorch`。

## 实际原因

国际在线 `ge.cri.cn` 对话中，模型提交了下列 XPath 扩展表达式：

```xpath
//div[contains(@class,'secondPage-details-1')]//*[re:test(text(),'\d{4}-\d{2}-\d{2}')]
```

HTML 引擎没有配置 `re` 命名空间，lxml 编译后在求值时发生
`Undefined namespace prefix`。旧工具丢失字段名和具体原因，并让整批测试
抛出 `ToolException`。后续模型换成标准 XPath 已恢复，因此不是模型 API 或
Clash 问题。证据为生产 run `845d78b9-1b2c-49e1-ae15-c76d3c181e44` 的
checkpoint 消息 19–20。

## 实现

- `http_summary.py`：有字段/范围上下文的静态诊断；支持 XPath 1.0/标准 CSS。
  提前检查语法、未支持的扩展函数/命名空间/变量和返回类型。不会自动改写
  选择器、绑定任意扩展，也不会将失败伪装成零匹配。
- `probe_http_select`：请求前预检；无有效字段时不访问网页。混合字段保留
  有效计数，返回 `validation_error` 和 `field_errors`，不能视作完整验证。
  JSON 响应明确提示 `http_api`，空 HTML 明确为响应问题。
- `tool_errors.py`：实际 MCP 调用边界统一处理 SDK 输入校验和运行时失败。
  参数校验先于操作执行；输出/内部失败不误报为可重试的输入错误。新诊断
  不回显原始异常、输入值、完整正文或请求 URL 中的秘密；有效字段仍可保留
  有界预览，不能据此把部分验证说成完整 PASS。
- GET 超时/临时 HTTP 错误提供最多一次重试指导；POST 和状态改变操作不建议
  自动重试。失效探针使用新的 `session_id`，不得重启已有爬取任务。
- 真实策略阻断优先保留为 `blocked/policy_blocked`、`retryable=false`；
  未知内部问题为 `tool_error/internal_error`，带安全 `incident_id`。
- 字段错误和样本总预览预算 6,000 字符，保留每个字段名、计数或错误代码。
  缩短预览不限制底层完整源检查，也不授权智能体读取完整正文补偿。
- 主控、分析/QA 修复子智能体及相关技能已更新同样的选择器约束与恢复指导。

`retry_limit` 是给调用方/智能体的恢复指导，不是服务端按对话计数的硬预算。
协议 `isError=false` 仅表示成功返回了回执，必须检查业务 `status`、质量验证
与完整性，不能当成爬取成功。

## 浏览器诊断

主页面策略阻断保留 `PolicyViolation`，不变成可重试网络错误；选择器错误
不回显 Playwright 调用日志。未知/关闭会话、导航超时有明确错误代码。

真实 Chromium 测试发现普通路由处理器仅拦截原生重定向的首个 URL。
探针改为 `route.fetch(max_redirects=0)`，主页面逐跳核验后发起独立新导航，
保持正确最终 URL、origin 和相对链接，限制次数并检测循环。
可选 iframe/资源/XHR 重定向保守阻止，Service Worker 禁用；这可能减少部分
动态网页的探测证据，并通过 `blocked_optional_redirects`/警告说明。
本次未改写执行器或宣称已完成全系统 SSRF 审计；原有 robots 重定向、DNS
检查与实际连接间的 TOCTOU 仍是独立限制。

## 实际 MCP 验收

使用生产命令/环境，但运行目录、记忆库隔离，未修改已输出的 10 条新闻或
生产站点记忆，也未重新执行用户的全量任务。

真实国际在线文章页验证：

1. 原 `re:test` 错误在网络请求前返回字段诊断，无 `ToolException`。
2. 混合测试保留 `title` 的 1 个匹配，仅 `date` 返回命名空间错误。
3. 标准 XPath 返回 `ready`，标题和正文容器均为 1 个匹配。
4. SDK 参数约束错误明确指向 `max_samples`。
5. 过期会话返回 `resource_error/session_not_found`。
6. 环回地址仍被阻止，不能重试。
7. 真实浏览器打开成功；无效浏览器选择器返回静态诊断，正常标题匹配通过。

验收约 **11.03 秒，模型调用 0 次**。报告：
`runtime/probe-error-verification/a14000728bab4265adcaa409859266ba/report.json`。

无收费模型重跑命令（默认 URL 是上述验收文章，可用 `--url` 指定其他 HTML 页）：

```powershell
& 'D:\conda_envs\mytorch\python.exe' scripts/verify_probe_errors.py --browser
```

根项目回归 **487 项全部通过，0 失败、0 跳过**。覆盖原功能、新选择器诊断、MCP 输入/输出错误、网络/会话/私网
分类、信息泄露防护、预览预算及真实 Chromium 重定向边界。
服务重启后请新建对话使用最新指令；旧对话里已经显示的错误不会被删除。

部署核对：default 与实际用户的主控和五个技能文件均与源文件一致，模型
配置未改变。服务已重启：8001 `/health` 返回 200，3001 未登录访问正常跳转
登录页并返回 200，Gateway 已加载 28 个 MCP 工具。
