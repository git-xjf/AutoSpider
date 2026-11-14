# 同站点子域名与 Clash Fake-IP 通用修复验收

日期：2026-10-04。执行环境：Windows / `mytorch`。

## 原因与处理

本机正在运行 Clash/Mihomo。中国日报子域名及 `ge.cri.cn` 的系统 DNS 返回
`198.18.0.0/15` 中的 Fake-IP，因此触发原有保留地址防护；原有起始主机精确
白名单又会阻止 `cnews.chinadaily.com.cn` 跳转到 `jl.chinadaily.com.cn`。
这是两层独立限制，不是模型 API 故障。

已在生产 DeerFlow MCP 配置启用：

```json
{
  "AUTOSPIDER_DOMAIN_SCOPE": "site",
  "AUTOSPIDER_FAKE_IP_MODE": "verified_public"
}
```

- 同站点范围使用离线公共后缀表，包含私有后缀：例如中国日报范围为
  `chinadaily.com.cn`，不会把整个 `com.cn` 或其他 `github.io` 租户放行。
- Fake-IP 域名自动通过固定 HTTPS DNS 服务校验 A/AAAA 和 CNAME 链；任何私网
  记录、超时或无法验证均拒绝。正常公网地址无需这项额外校验。
- 新草稿自动生成同站点范围；旧记忆执行时仅升级隐式起始主机范围的副本，
  不修改原始记忆。自定义白名单保持不变。
- 已同步 default 和实际用户的 crawler-lead、分析子智能体及相关工作流技能。
  服务器接受的同站点/Fake-IP 不再要求逐域授权；真正策略拦截不进行选择器
  修复或换模式反复重试。
- 独立调用未配置上述选项时，仍为 `host` / `strict`。

## 验收结果

- 根项目测试：**387 通过，0 失败、0 跳过**。
- 真实 Chromium 导航拦截、弹窗兼容及 HTTP 详情执行均有回归测试。
- 使用生产 MCP 命令和环境启动真实 stdio 会话，隔离运行目录和站点记忆库。
  下列只读 HTTP 探测全部为 `ready` / **200**，保留 robots 与私网检查：

| 目标 | 用途 |
|---|---|
| `https://cnews.chinadaily.com.cn/5bd5693aa3101a87ca8ff676` | 原列表页 |
| `https://jl.chinadaily.com.cn/` | 同站点子域名 |
| `https://sx.chinadaily.com.cn/` | 同站点子域名 |
| `https://cn.chinadaily.com.cn/` | 同站点子域名 |
| `https://ge.cri.cn/policy` | 新站点 / 自动 Fake-IP 核验 |

中国日报三项子域名探测共用新草稿派生的 `['chinadaily.com.cn']`，不是分别
扩大白名单。环回 IP、无关站点及显式窄白名单的三个负向用例均按预期阻止。
整个 MCP 验收约 **10.34 秒，模型调用 0 次**；未对生产记忆库新增记录。
报告：`runtime/network-verification/4bf1a8d8d70e4069a0bfdee9e19f1a1d/report.json`。

可重跑同样的无模型验证：

```powershell
& 'D:\conda_envs\mytorch\python.exe' scripts/verify_network_profile.py
```

服务已按 8001 / 3001 端口重启：Gateway `/health` 返回 200，前端未登录访问
智能体页面正常跳转登录页并返回 200，Gateway 已加载 28 个 MCP 工具。
模型与凭据配置未改变。本次验证网络范围和防护边界，不声称已完成上述站点
的整站正文爬取；子域名页面布局差异仍需 QA。

## 使用边界

请新建对话或新草稿再爬取；旧的终态失败 ID 不会被重置或自动复活。
无需为了上述兼容关闭 Clash，但此便利模式明确依赖**可信本机代理**：公网
DNS 核验不等同于锁定代理最终连接的 IP。真实私网、无关站点、验证失败、
robots 禁止、登录/验证码/服务器访问控制仍不会自动绕过。

协议依据：[Mihomo DNS 配置](https://github.com/MetaCubeX/Meta-Docs/blob/main/docs/config/dns/index.md)、
[Google Public DNS HTTPS JSON API](https://developers.google.com/speed/public-dns/docs/doh/json)、
[tldextract 官方文档](https://github.com/john-kurkowski/tldextract/blob/master/README.md)。
