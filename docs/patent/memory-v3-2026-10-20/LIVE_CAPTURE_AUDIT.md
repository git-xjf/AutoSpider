# CLS 最小公开访问核实（2026-10-20）

> 最新资源阶段：用户继续后按限定范围补采，两详情原字节已保存且匹配旧原始哈希；`wwwjs.cls.cn/robots.txt` 返回 403，立即停止，未下载脚本／启动原站浏览器。[完整回执](../memory-live-2026-10-21/RESOURCE_CAPTURE.md)。旧访问记录和冻结实验不覆盖。

> 2026-10-21 后续：用户已明确允许单次隔离公共 DNS 核验。已通过核验与 robots 检查，修复缺 MIME 文档归档后保存 1 个栏目页、2 个详情页；两详情为脱敏保存版本。整轮 8 次 robots／文档 HTTP（含首次未保存正文的 2 次），无浏览器子资源或原站回放。临时模式已恢复，默认仍 strict，未改配置／信任名单。完整记录见[真实输入验证结果](../memory-live-2026-10-21/RESULTS.md)及[来源审计](../memory-live-2026-10-21/SOURCE_AUDIT.md)。以下历史阻断和待授权记录原样保留，不代表最新采样状态。

按用户“优先使用项目已有公开新闻站”的选择，本次只做无鉴权 HTTP GET 与公开搜索索引核实。没有读取用户 profile、凭证、历史会话或 token 日志，没有携带登录 Cookie，没有访问 robots 禁止的验证页面，没有重试挑战或改动 V3 实验。

结果是可以取得公开原始响应，但目前没有保存原始正文/脚本文件，也没有完成浏览器资源闭包或真实站点离线回放。以下 hash 是本次响应原始字节的 SHA256，不是独立正文真值或网站真实性认证。

必须并列保留两个事实：通用无鉴权 `httpx` 请求返回了下表响应；项目实际 `assert_public_host('www.cls.cn')` 则在当前 strict 模式因解析到 `198.18.0.29` 而拒绝。后一个是主代理的独立项目检查结果，本次只读源码确认 strict/Fake-IP 门限，没有再次解析或请求网络。HTTP/TLS 可达不等于通过 AutoSpider 的 guard；当前项目采集应 fail closed，不能更改环境配置、信任表或直接换通道绕过后再宣称成功。得知阻断后已停止新增网络请求。

| 公开 URL | 本次状态 | 原始字节 | SHA256 |
|---|---:|---:|---|
| `https://www.cls.cn/robots.txt` | 200 | 517 | `c1556d4b2bfc308333797146669a697db0795dfdd6dbb039bf57a688595ee0fa` |
| `https://www.cls.cn/depth?id=1118` | 200 | 10,226 | `9a44941635c357091ac3f7b27e250137afc2422ae655a3bda451982d379a0696` |
| `https://wwwjs.cls.cn/_next/static/chunks/pages/depth-faf9edd9870f0738.js` | 200 | 22,635 | `c6e9c5ece570a831efbfb4ef8dce5bad1f773dcb9a9eedf840814a0a71860da8` |
| `https://www.cls.cn/detail/2501657` | 200 | 15,663 | `1301cb94eaea79627de4762d2df2ef76b6a5422ec25b5833bc44cd819c0d43e6` |
| `https://www.cls.cn/detail/2470844` | 200 | 29,969 | `e4fe3fb2ba3dde8d21fe4cd2df4db3701411bdd326baca570788a2107bf6b60b` |

这五个地址各作一次直接 GET；两个 detail URL 来自公开搜索索引，不是假定 `depth` 当前列表包含它们。它们分别是短讯与较长文章，可作为后续 fresh-browser 小范围采集候选，不能说已证明同模板、同栏目或自然变更关系。

发现详情 URL 使用的公开搜索查询是 `site:cls.cn/detail "财联社" "2026" "券商"` 与 `site:cls.cn/detail "2026年10月"`；返回的已索引文章包括 [2501657](https://www.cls.cn/detail/2501657) 与 [2470844](https://www.cls.cn/detail/2470844)，随后分别作一次 GET 核实。没有猜递增 ID，也没有把搜索结果伪装为 `depth?id=1118` 的现存列表项。

## robots 与响应边界

本次 [robots.txt](https://www.cls.cn/robots.txt) 对通用 agent 的规则允许 `/depth`、`/detail`；禁用若干验证/特殊文件和图片后缀。没有观察到这次允许路径返回 401、403、429 或 challenge 标记。没有触发对受禁验证路径的访问。robots 的允许不等于内容开放许可证，也不保证未来请求持续可用。

robots 返回 `text/plain; charset=UTF-8`，已观察到的 depth JS 返回 `application/javascript; charset=UTF-8`。本次三个 HTML 响应没有提供 Content-Type；默认文本解码得到的中文标题含替换字符。后续采集器必须先保存原始 `response.body()` 字节，并核对 BOM/meta/实际编码，不能直接将默认 `response.text` 的有损结果作为可回放源。本次没有再请求同一 HTML 来猜编码，也不推定某一种编码已经确认。

## 公开可观察结构

`depth?id=1118` 的原始 HTML 含 `__NEXT_DATA__` 和 9 条公开 script URL，没有发现当前响应内的详情 `<a href>` 列表。这说明仅从这份源 HTML 的 a 链接不足以取得当前详情集合；后续可让隔离浏览器观察客户端公开渲染/请求，而不是猜 URL 或补造列表证据。

两个 detail 响应都含 `__NEXT_DATA__`，其顶层为 Next.js 页面状态，`props.pageProps` 的公开键包括 `query`、`articleDetail`、`error`、`columnDetail`、`initialState`，页面 route 为 `/detail/[id]`。它们各引用 18 条脚本，其中共同的 detail chunk 是：

`https://wwwjs.cls.cn/_next/static/chunks/pages/detail/%5Bid%5D-52109977442be368.js`

本次只报告该已观察到的 URL，未继续下载它或运行其中代码。

公开 depth chunk 中出现以下 API/路由字面路径：

- `/v3/depth/home/assembled/`
- `/v3/depth/list/`
- `/v2/article/hot/list`
- `/api/subject/hotAndNewestSubject`

这些只是公开代码中的字符串，没有据此猜服务 host、构造签名或主动调用 API，因此不能写成“已验证可无鉴权调用的 JSON API”。真正使用时需记录 fresh-browser 实际发起的公开请求及响应，并检查是否涉及受保护流量；不保存登录 Cookie、Authorization 或签名密钥。

## 对真实回放实验的含义

公开详情页现在具有原始响应采集可行性，但最小动态 capsule 仍未建立。需要隔离浏览器取得入口 HTML、实际执行脚本与所用公开数据响应、最终渲染候选、状态/编码/请求方法，以及各资源 hash；未记录资源、验证码、认证或策略拒绝都应直接形成不完整/受阻结果。

这里的“响应可取得”仅指此前通用 HTTP 探测，不能覆盖当前项目 guard 的阻断。隔离采集器应先检查项目策略，保存真实阻断回执并停止，而不是为完成 capsule 放宽配置。`packages/crawl_core/policies.py:assert_public_host` 默认 strict，并将 Fake-IP 的管理员配置/验证路径与普通公网放行分开；本次没有修改这些路径，也没有授予例外。

不能把 `articleDetail` 的公开内联状态直接称为独立正文语义真值；它至多是出版社提供的数据源。现有 V3 `Binding` 读取完整 JSON 响应，不能未经实现/验证就声称能从 HTML 内 `__NEXT_DATA__` 正确抽取、干预并回放。SSR 内容、hydration、正文 HTML 变换和未捕获依赖可能使严格的纯文本差分合同无法认证，这应作为实际结果保留。

这次核实不产生真实站效果指标。后续若取得可闭包重放的公开 capsule，应称“真实来源、隔离离线回放的字段绑定实验”；即使字段绑定成立，也不能升格为正文语义正确或新机制具备专利创造性的证明。

## 受项目guard保护的采集器实际运行

后续新增 [capture_public_memory_pages.py](../../../scripts/capture_public_memory_pages.py) 并执行了严格预检。最终版本回执在 [live-cls-20261020-final-preflight/report.json](../../../runtime/experiments/patent-memory-v3-20261020/live-cls-20261020-final-preflight/report.json)：

- `status=policy_blocked`，原因仍为 `www.cls.cn → 198.18.0.29`。
- `network_requests=0`、`network_body_bytes=0`；这两个字段专指robots/文档HTTP，DNS检查不计入该数。
- 没有访问robots/文章，没有取得渲染DOM、脚本/响应闭包或HAR，`replay_ready=false`。
- 采集器源码SHA256为 `264d3df197618d3b868e6eac7d2d04a3f61a7c3d88ccc7b94ac6bea9dbd47523`，与最终回执记录一致。

采集器只实现最小的有界HTTP原文档采集，不是完整浏览器采集器。后续即使通过公共DNS及robots，也不能把它的输出直接称为v3可重放Bundle。它使用显式域名/方法/端口范围、敏感query/headers/text脱敏、robots无法核验即停止、原始/脱敏字节分别hash，以及BOM/header/meta严格编码校验；不使用 `errors=replace` 保存有损正文，不猜详情ID，不覆盖旧证据目录。

独立安全与编码测试26项通过；在加入这些测试后，全项目实测 **613 passed, 1 warning / 56.58秒**，警告仍是既有pydantic-settings前向引用提示。原v3矩阵及其源码/结果未更改。

用户已被询问是否允许仅本次隔离进程使用项目已有的 `verified_public` 公共DNS核验模式；在明确授权前不切换mode，不改配置文件或信任名单。核验若失败仍应阻断。另一个安全输入途径是用户提供去除凭证的完整快照/HAR。
