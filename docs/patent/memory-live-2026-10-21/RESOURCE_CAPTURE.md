# 后续资源采集：原字节补齐，CDN robots 403 后停止

日期：2026-10-21。用户在明确的两详情页、三个精确主机、最多 80 次 GET／20 MiB 应用层响应正文／120 秒范围后要求“继续”，本轮沿该范围执行。没有扩大到其他站点、读登录状态或修改配置。

## 1. 实际执行结果

| 尝试 | 证据目录 | 目标站 GET | 已消费响应正文 | 新保存正文资源 | 浏览器启动 |
| --- | --- | ---: | ---: | ---: | --- |
| 沙箱内核验 | `authorized-browser-r1/` | 0 | 0 B | 0 | 否 |
| 经执行权限审核重试 | `authorized-browser-r2/` | 4 | 46,149 B | 2 | 否 |

两目录均位于 [实验根目录](../../../runtime/experiments/patent-memory-live-20261021)。首次在公共 DNS 核验传输阶段失败，没有得到目标站 HTTP 响应；不能将其当成远站拒绝。权限审核后同一脚本完成公共 DNS 核验，按顺序取得：

1. `www.cls.cn/robots.txt`：200，规则允许两个详情路径。
2. `www.cls.cn/detail/2501657`：200，保存 15,663 B 原响应。
3. `www.cls.cn/detail/2470844`：200，保存 29,969 B 原响应。
4. `wwwjs.cls.cn/robots.txt`：403，立即停止。

403 的正文未读取／保存；46,149 B 为已消费应用层正文，等于 517＋15,663＋29,969，不是包含 TCP、TLS、HTTP 头或 DNS 的线上总流量。80 次 GET 与正文预算包含本脚本的目标 robots／资源请求，不含项目独立公共 DNS 核验。本轮实际远低于所有上限。

没有发送任何脚本 GET，没有启动浏览器，没有运行原站 JavaScript，没有生成 HAR 或资源闭包。403 仅证明此次 robots 端点拒绝，不能扩大为“该主机永久禁止全部自动访问”；但按本实验的 fail-closed 门槛，已足以停止。未更换 UA、切换别名、猜 API 或绕过该拒绝。

报告状态为 `PENDING_INCOMPLETE`、`browser_started=false`、`replay_ready=false`。两次进程都在 `finally` 恢复临时核验模式，报告 `mode_restored=true`、`mode_after=strict(default)`；父进程环境未设置该模式，配置／信任名单／生产记忆库未改。

此前小规模采样共 8 次目标 HTTP；加本轮 4 次，10-21 两轮累计 12 次、已消费正文 114,301 B（均不含 DNS／文献请求或 10-20 历史探测）。保存页面文件累计 5 份，但仍只涉及 3 个不同页面，不算 5 个独立站点样本。

## 2. 新补齐的原字节证据

两份新增响应经过保守凭证特征筛查，正文未改写；原始／保存 SHA256 相同，且与前一批报告已记录的原始 SHA256 一致：

| 页面 | 新文件（r2 目录下） | 原始／保存 SHA256 |
| --- | --- | --- |
| 2501657 | `resource-000.bin` | `1301cb94eaea79627de4762d2df2ef76b6a5422ec25b5833bc44cd819c0d43e6` |
| 2470844 | `resource-001.bin` | `e4fe3fb2ba3dde8d21fe4cd2df4db3701411bdd326baca570788a2107bf6b60b` |

这是新增取得、以旧原始哈希核对的响应，不是从哈希反推字节，也没有覆盖／重新标注旧脱敏快照。[纯离线核对输出](../../../runtime/experiments/patent-memory-live-20261021/cls-browser-input-audit.json)确认两页均：

- 当前 `redact_text` 对新原文的处理，精确复现旧保存字节。
- 完整解析后的 `__NEXT_DATA__` 无变化，正文字符串、标题、实体 ID 均未改变。
- 正文 HTML→文本归一结果不变。这里指本次哈希匹配的两份样本，不推广为所有脱敏处理都无损。

因此，上一批审计中“原字节尚不可取得、字段是否改变不能核对”的限制，对这两份哈希匹配样本已有后续补证。旧文件仍保持 `body_redacted=true`；不把它们改称原字节文件。原文／静态表示一致不增加独立语义参考数，也不证明运行时字段绑定。

离线审计脚本 [audit_cls_resource_attempt.py](../../../scripts/audit_cls_resource_attempt.py)只输出长度、哈希、JSON pointer 与关系布尔值，不复制新闻全文；输入与源码运行前后哈希守卫通过。复核使用新输出文件名，拒绝覆盖已有证据：

```powershell
python scripts/audit_cls_resource_attempt.py --output runtime/experiments/patent-memory-live-20261021/review-new-name.json
```

## 3. 实现与验证边界

新增隔离研究工具 [capture_cls_browser_resources.py](../../../scripts/capture_cls_browser_resources.py)，不接入生产模块。它使用精确主机／HTTPS／无请求体 GET 范围、逐项公共 DNS 与 robots 核验、累计限额、无自动重定向及全新无 Cookie HTTP 客户端。发现认证、验证或限流即停止；敏感正文不保存、不改写、不交给浏览器。凭证筛查可能有误报，不能宣传为通用完备秘密扫描器。

浏览器分支保持 offline、拦截全部请求、阻断 service worker／WebSocket 等未支持通道；下载层只考虑 document／script／stylesheet／xhr／fetch，图片等其他类型被阻断并记为不完整。响应正文以原字节提供；遗漏 Set-Cookie 等敏感／传输头是显式约束，不声称完全保留原服务器会话语义。即使有限执行干净，也需独立离线复核，采集器始终不直接给出 `replay_ready=true`。

本轮真实站流程在浏览器分支前停止，因此新增实际 Chromium 测试**只使用合成 HTML／脚本与 mock HTTP**，核验离线拦截、脚本执行、原头／字节提供、缓存共享及候选哈希记录。该测试不是 CLS 原站回放，不能转成站点效果。

本轮新增 37 项测试（31 项采集边界／合成浏览器测试，6 项纯离线核对测试）均通过。最终全项目实测 **685 passed, 1 warning / 64.77 秒**，警告仍是既有 pydantic-settings 的 `lifespan` 前向引用提示。首次测试遇旧系统临时目录 ACL 拒绝后，改用工作区内新建临时目录及禁用 pytest 缓存重试；未修改系统目录权限或清理旧证据。

## 4. 冻结证据

| 对象 | SHA256 |
| --- | --- |
| 采集脚本及 r1／r2 各自 `collector-source.py` | `b9173c7b7932b1783fb00853a7d4907fd9326f2e6ab0da3a8c2d6717ec3994aa` |
| r1 报告 | `d61b24dcde6cc9e84b71ff5ef19d2fec2f68d80fe2bc88feefbf2cb7747fd162a` |
| r2 报告 | `90b34ec25003c0c130a60f0332a9cdf05e2acc117adceceda26bdfb0d868a94a` |
| 新离线审计脚本 | `95167594a570622b9b219e92d0b20fac78ab807e01f5b590da0691b9dc06ff5e` |
| 新离线审计 JSON | `57129aba61832048f877a6504a9b229a132f4397de47ef120531aaf86d988019` |

此前冻结 v3、旧采集回执和旧审计 JSON 不变；旧采集器源码档案与新脚本分别归档，不能相互替代版本。

## 5. 对专利研发的实际影响

补齐了两份原始 HTML 证据及脱敏影响核对，但尚未取得真实执行脚本、运行时数据和阶段轨迹。没有证据证明原站发生了 hydration 恢复，也没有可比较的新机制效果，不能把 HTTP 403、采集安全门槛或新文件数量当作专利创新。

[既定研究问题与强基线门槛](RESEARCH_BOUNDARY.md)保留；不凭缺资源继续编造权利要求或一轮获胜实验。要继续真实机制验证，需要用户提供有权用于研究、已去凭证的实际浏览器资源包，或另选允许取得完整资源的目标。单独放宽 DNS／UA／robots 规则不能代替缺失输入。
