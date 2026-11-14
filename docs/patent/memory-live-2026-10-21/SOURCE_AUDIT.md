# CLS 公开响应快照离线来源审计（2026-10-21）

> 后续补证：已新增取得两份详情原始响应，与本审计记录的原始哈希一致；离线比较证明旧保存版本的完整 `__NEXT_DATA__`、正文、标题和实体 ID 未改变。见[资源采集／字节核对](RESOURCE_CAPTURE.md)。本审计输入和正式 JSON 未覆盖，旧详情仍属脱敏版本；下面“无法核查具体字段是否改变”是当时的证据限制。脚本主机 robots 403 后已停止，资源闭包仍为 0。

## 结论和对象边界

已离线审计 3 份保存文档：1 份列表原字节文档、2 份**脱敏后的公开详情响应快照**。均能解析出唯一的 `script#__NEXT_DATA__`；两详情的 `articleDetail` 均为单个对象，而非记录数组。一篇正文只有内嵌数据未找到同文本 SSR 正文，另一篇存在内嵌 HTML 正文与 SSR 正文的同页文本一致性。两者都不能无改写接入冻结 v3 的整响应 JSON／记录数组／纯文本合同。

本审计没有联网、执行 JavaScript、启动浏览器、读取凭证或补抓资源。它只证明所保存文档的结构和同页归一化文本关系，不证明字段因果来源、新闻语义正确性或新颖性。详情快照的原始字节未保存，不能把它们称为原站原字节完整复现。

## 可复核产物

运行命令（输出使用独占创建，已有文件不会覆盖）：

```powershell
python scripts/audit_cls_source_documents.py --output runtime/experiments/patent-memory-live-20261021/cls-source-audit.json
```

脚本：`scripts/audit_cls_source_documents.py`；结果：`runtime/experiments/patent-memory-live-20261021/cls-source-audit.json`。脚本只输出结构、长度、SHA256、公开实体 ID、公开输入 URL 与关系布尔值，不复制新闻全文。输入文档、采集报告、脚本和当前 v3 源码均有运行前后字节哈希守卫；实际运行返回 `all_guarded_bytes_unchanged`。

- 审计脚本 SHA256：`56bbb9b4047728244aa345b5feb1037d55d717965b0464c614b8cc5c70fec0be`
- 审计 JSON SHA256：`ab21407c72f356205d6b7d1dc1e18cdeca096de0885119a28bb2c86c1676612e`
- 本次核查的 v3 `replay.py` SHA256：`b80741b0d10e06f4c53647e669c24025e629f5be7cc348880e10c5ee117b0c36`
- 三份报告记载的采集器 SHA256：`d627da1398642ba0f10115aa2b5fa93c2843ae44700f62dd8cd9059909cb5db6`

所有采集输入位于 `runtime/experiments/patent-memory-live-20261021/authorized-public-batch-r1/`。报告记录 HTTP 200、`public_host_verified=true`、robots 允许；HTTP 缺少 Content-Type，由严格 UTF-8 解码和 HTML 文档签名仅作归档识别。这不是浏览器执行许可、MIME 响应头的补造或依赖采集成功证据。报告均明确 `javascript_executed=false`、`rendered_html_captured=false`、`replay_ready=false`。

## 输入完整性

| 输入 | 公共 URL | 报告采集时间（UTC） | 原／存字节数 | 文档状态 |
| --- | --- | --- | --- | --- |
| list | `https://www.cls.cn/depth?id=1118` | 2026-10-21 02:05:32 | 10226／10226 | 原／存 SHA256 相同，`body_redacted=false` |
| detail-2501657 | `https://www.cls.cn/detail/2501657` | 2026-10-21 02:05:33 | 15663／15663 | 原／存 SHA256 不同，`body_redacted=true` |
| detail-2470844 | `https://www.cls.cn/detail/2470844` | 2026-10-21 02:05:34 | 29969／29971 | 原／存 SHA256 不同，`body_redacted=true` |

具体时间以 JSON 中完整 `requested_at_utc` 为准。三个保存文档的实际 SHA256 均等于对应报告的 `saved_sha256`：

- list：`9a44941635c357091ac3f7b27e250137afc2422ae655a3bda451982d379a0696`
- detail-2501657：`3ba88d318f6412ca3aae9d62ddfd5d85e693ff5a7c248f1c4fbd1447a48e5c05`
- detail-2470844：`9f30d4261d03ae93f087812b9af5cfc9bc6d1ab6c1a05b00d9ce3d3fac819121`

对应采集报告 SHA256：

- list：`90c97eed5e6bff0e0b9d1ce77bef19ee2ec76c859562347e5ccca940683c9765`
- detail-2501657：`e8c55f9045fafc90a28a5341fc82c4afebab98a16d4769bc91a0f66f2df05731`
- detail-2470844：`33759888635df8b2d176751292bc3878ecd9d1371c6403026e0b11a2aa770e18`

三份文档均未发现字面量 `[REDACTED]`，但这**不**撤销详情报告的脱敏状态。原／存哈希不同已足以否定字节恒等；缺少原始字节，无法核查脱敏具体改动范围，也不能断言正文或内嵌数据字段完全未变。现在只能证明保存版本中的 JSON 可解析。

## 实际结构与同页对照

字段长度均为 Python `len(str)` 的 Unicode 码点数；正文归一化为 lxml HTML fragment 的 `text_content()` 后折叠空白，不进行语义评审。

| 项目 | list | detail-2501657 | detail-2470844 |
| --- | --- | --- | --- |
| `__NEXT_DATA__.page` | `/depth` | `/detail/[id]` | `/detail/[id]` |
| `pageProps` | 仅 `initialState` | `query,articleDetail,error,columnDetail,initialState` | 同左 |
| `/props/pageProps/articleDetail` | 不存在 | `dict` | `dict` |
| `id`／`query.id`／URL ID | 不适用 | 字符串归一后均为 `2501657` | 字符串归一后均为 `2470844` |
| `title`／`brief` 长度 | 不适用 | 26／222 | 24／58 |
| `content` 长度／类型 | 不适用 | 228，字符串，解析后无元素标签 | 3072，字符串，含 HTML 元素 |
| HTML 正文元素 | 不适用 | 无 | 31 `p`、9 `h4`、4 `strong`、1 `img` |
| HTML 正文归一化文本长度 | 不适用 | 228 | 2590 |
| `.article-title` | 无 | 无此类，但另一个通用 class 的 SSR div 与 JSON title 相同 | 1 个，其文本等于 JSON title |
| `.detail-content` | 无 | 0 个 | 1 个，归一化文本等于解析后的 JSON content |
| 完整正文文本精确相等的静态 DOM 节点数 | 不适用 | 0 | 2（外正文 div、内层 div） |
| 引用的外部 script 数量 | 9 | 18 | 18 |

两篇 `isFree` 均为 `true`，`error.errno` 均为 `200`；这只是同一响应声明的值，不升级为独立真实性或可获取性证明。脚本为全部 `articleDetail` 属性提供 JSON pointer／类型／长度／哈希，正文不会进入输出。

2501657 的标题出现在通用 class 的 SSR div 中，不能把 `.article-title` 缺失误报为所有 SSR 标题缺失。该详情在排除 `head/script/style/noscript` 后没有与完整正文（或 brief，另作只读检查）归一化精确相等的 DOM 节点；这只说明本次**保存静态文档**没有此项精确对照，不等于浏览器渲染后必定没有正文。

2470844 中 `.detail-content` 和它的直接子 div 都等于 2590 码点正文，标题的外 div 与内 span 也同值。因此，文本相同不能唯一确定 selector 粒度，也不能证明 hydration 实际从哪个输入取值。JSON content 的原字符串含标签，不能直接当作冻结 v3 合同要求的 DOM 纯文本。

三份保存文档的 `a[href]` 均未发现 `/detail/数字` 路径。两详情来自此前公开搜索发现并被单独明确采样，不是本次 depth 列表中已验证的链接；本审计不建立列表归属关系。

## 来源独立性及 v3 适配边界

JSON 与 SSR 来自**同一份保存 HTTP 响应**，不是两份独立证据。当前独立语义参考数为 0；完整浏览器资源 capsule 数为 0。HTML 中引用的 script URL 只被离线记录 host/path/hash，不进行访问，也不将路径字符串声称为已验证的公开 API。

冻结 v3 的 `_prepare` 对绑定 `Resource.body` 整体执行 `json.loads`，随后要求 `records_pointer` 指向非空数组，并对每条记录的字符串字段做纯文本合同；干预时重序列化整个 JSON response。现有 CLS 入口响应为 HTML、`articleDetail` 为 singleton，而且没有对应 JavaScript／API 响应等运行依赖，故三份文档无一可直接作为有效的原站回放 capsule。不得制造 `source.json` 并改页面 fetch、把 singleton 人工包装为数组、或注入自写 renderer 来假装原页面机制验证。

最小后续研发应是独立版本的 inline-source lens：显式绑定入口响应、唯一 script、精确字段 JSON pointer 与公开实体 ID；只修改 script 中指定字段、保留 SSR 与其余已采得资源不动。它需要单对象合同和明确版本化的 HTML-content→text 变换合同，而不是偷偷改变旧 v3 的含义。对照字段必须来自实际保存数据；若使用 title/brief 负对照，应声明是同对象其他字段，不冒充真实 foreign entity 控制。

但目前不能运行该实验：真实浏览器依赖未采得，原响应字节也不完整。应先由用户另行授权有界且合规的资源采集，失败或缺资源保持 PENDING，不能用本次静态审计补成成功回放。

## 判别性实验建议（尚未实施）

在确有完整、版本固定、许可范围明确的离线 capsule 后，以相同候选和资源分别执行：原样 baseline 两次、仅改 inline JSON 正文字段、仅改 SSR 正文、实际其他字段负对照。可另做 JSON 与 SSR 同时改动的渲染存活阳性对照，但它同时改了输入与输出，**不得**作为字段来源因果证据。

若只改 JSON 而正文不响应，或出现 hydration／资源缺失／业务变换不满足合同，则应输出 PENDING 或该源合同不支持；不能认定原站有错，也不能强制让方法通过。成本需完整记录采集、解码、结构提取、资源审计、浏览器加载和候选观察，不能把静态表示一致性包装成 HTTP／LLM 节省。

本次所得仅是两个公开页面保存版本的工程输入适配证据，不是第三方盲测、自然漂移准确率、自动语义真值、已集成生产能力或可授权专利结论。
