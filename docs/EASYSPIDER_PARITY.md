# EasySpider 功能兼容矩阵

目标是让 AutoSpider 在“可完成的浏览器采集任务”层面覆盖 EasySpider，同时保留 DeerFlow 多智能体、CrawlSpec/CrawlPlan、QA 修复和站点记忆。状态含义：`完成` 已有代码与测试；`基础` 已有执行原语但仍需更多站点回归；`计划` 尚未实现。

| 能力 | 状态 | AutoSpider 实现 |
|---|---|---|
| 打开网页、等待页面 | 完成 | `OPEN_URL`、`WAIT` |
| XPath/CSS/JSONPath 与 fallback | 完成 | HTML/Playwright Locator + 安全 JSONPath 子集 |
| 文本、HTML、链接、图片/任意属性 | 完成 | `EXTRACT` |
| 重复元素循环 | 完成 | `LOOP_ELEMENTS` |
| 列表→详情→返回 | 完成 | 同标签和新标签页栈 |
| 点击翻页 | 完成 | `LOOP_PAGES` + `CLICK` |
| URL/API 翻页 | 完成 | `url_template`、页码和游标，含 JSON GET/POST E2E |
| 无限滚动 | 基础 | `SCROLL` + 页面变化检测 + 去重 |
| 输入、下拉框、键盘、悬停 | 完成 | `INPUT`、`SELECT`、`PRESS`、`MOVE`，已有 Chromium E2E |
| 条件分支 | 基础 | Selector 存在条件；表达式条件待扩展 |
| JavaScript 自定义操作 | 完成 | `CUSTOM`，默认安全关闭 |
| Python/系统命令节点 | 计划 | 将采用隔离 worker 与能力白名单，不直接开放宿主 Shell |
| 页面和元素截图 | 完成 | 全页和元素截图 |
| JSON/CSV/XLSX | 基础 | 原生输出与 MCP 结构化结果；JSON 已有 E2E |
| MySQL 输出 | 计划 | 计划实现独立 sink 插件 |
| 多起始 URL/参数化任务 | 基础 | execution parameters，待补并发批次策略 |
| Cookie/登录会话复用 | 基础 | 仅 Playwright `storage_state`；直接 HTTP 模式拒绝持久化秘密，请求会话管理 UI 待补齐 |
| HTTP/HTTPS/SOCKS 代理 | 基础 | execution proxy parameters，待代理池健康检查 |
| Chromium/Firefox/WebKit | 基础 | execution `browser` 参数；当前安装和回归基线为 Chromium |
| iframe | 完成 | 显式 `frame_selector`，支持帧内提取、截图和操作 |
| Shadow DOM | 基础 | CSS Locator 可穿透开放 Shadow DOM；封闭 Shadow DOM 不保证 |
| 文件下载 | 完成 | `DOWNLOAD`、安全文件名与运行目录，已有 Chromium E2E |
| 图片批量下载 | 计划 | 计划加入资源 sink |
| OCR | 计划 | 计划作为可选 OCR provider 插件 |
| CAPTCHA 第三方服务 | 计划 | 仅允许合规站点和人工/第三方 provider，不提供绕过能力 |
| 定时执行 | 完成 | DeerFlow 2.0 Scheduled Tasks，安装配置默认启用 |
| 多任务并发 | 基础 | MCP 内异步 job + DeerFlow durable MCP task；独立浏览器进程池待补齐 |
| 断点恢复 | 计划 | 当前任务/状态持久化，记录级 checkpoint 待补齐 |
| 可视化流程设计器 | 计划 | 优先做 CrawlPlan Web 编辑器，不复制 EasySpider UI |
| CLI/服务调用 | 完成 | `autospider` CLI 与 MCP stdio/HTTP |
| Sample→QA→Repair→Full | 完成 | DeerFlow 三智能体闭环 |
| Selector 站点记忆 | 完成 | SQLite Site Memory |
| XHR/Fetch API 发现 | 完成 | 有界网络探测、脱敏预览、JSONPath 验证与 `http_api` 执行器 |

## 验收原则

每项标记为“完成”必须同时具备：稳定 IR、确定性编译、执行实现、单元测试、浏览器 E2E 和 MCP 回归。真实网站变化快，因此“能力完成”不等于承诺任意网站 100% 成功；最终仍以 sample QA 是否通过为准。

## 后续顺序

1. FramePath、下载清单、Cookie profile 与调度。
2. 表达式条件、隔离脚本 worker、MySQL/资源 sink。
3. 并发租约、checkpoint 和认证会话管理。
4. CrawlPlan 可视化设计与运行监控界面。
