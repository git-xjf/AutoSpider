# V3 数据来源审计（2026-10-20）

本次目标是为 AutoSpider 的一个可辩护 memory 专利方向寻找真实网页来源与独立评分依据。它不是建立论文排行榜，也不把历史抽取结果重新命名为人工真值。本次只读盘点，唯一新增文件为本报告；没有改生产模块、V1/V2 输入或结果，也没有下载整套外部语料。

## 本地结论

在下表六个可访问的项目运行/验证目录内，找到 146 个待分类的非标准 `result.json` JSON 路径，但没有发现可直接复用的原始目标新闻网页 HTML、HTM、MHTML 或 HAR。文件数是路径筛选结果，不表示 146 份原始响应；其中还含任务、执行状态、草稿和自定义文件名的抽取输出。未读取文章结果正文，未将 `result.json`、自定义抽取输出、QA PASS 或 selector sample 当作 source HTML/人工真值。

| 目录 | 筛选后 JSON 路径数 | HTML/HTM/MHTML/HAR 文件数 | 可用的原始目标网页+独立正文真值对 |
|---|---:|---:|---:|
| `runtime/playwright` | 95 | 0 | 0 |
| `runtime/verification` | 37 | 0 | 0 |
| `runtime/generic-verification` | 7 | 0 | 0 |
| `runtime/network-verification` | 2 | 0 | 0 |
| `runtime/probe-error-verification` | 1 | 0 | 0 |
| `runtime/outputs` | 4 | 0 | 0 |

另外发现 33 份专利/论文来源 HTML，位于 `runtime/experiments/patent-memory-20261019/sources`，以及 11 份 `tests/websites` 本地构造网页。前者是文献检索资料，后者是工程 fixture，均不能计入目标新闻站真实采集正文实验。33 份资料的本地修改时间为 2026-10-19 20:18–20:23（北京时间）；这是文件时间，不是可核验的原网站采集时间。

本次不读取聊天回放、token/模型使用日志、凭证配置或用户会话内容。补充搜索 `.research`、`.tmp`、`data` 中的 HTML/snapshot 文件时，`.tmp/pytest-of-DELL` 返回拒绝访问；没有更改权限或绕过，因此不对该不可访问区域作“没有文件”的断言。V1/V2 manifest 中的 HTML 字符串是已知生成数据，排除为真实来源证据。

### 已有验证材料能证明什么

2026-09-11 两次 `cls-1118` 验证的公开目标路径为 `https://www.cls.cn/depth`（报告不输出查询参数）。两份 `selector_evidence.json` 都只有 `valid / match_count / samples`，两类 manifest 只有 `execution_id / task_id / seconds / count`。它们支持“当时执行了选择器/采集检查”，不保留完整输入 DOM、HTTP 响应字节或独立正文标注。

2026-10-04 的 network 验证报告只记录探测 URL、状态、状态码和耗时；2026-10-05 的 probe-error 验证报告记录参数/字段错误、计数与重试边界。两者均未见原始响应正文。上述时间取文件元数据，不推定为独立可信采集时间戳。

| 相对路径 | 最后修改时间（UTC） | SHA256 |
|---|---|---|
| `runtime/verification/cls-1118-20260911-184318/selector_evidence.json` | 2026-09-11 10:43:25Z | `c5d4c3e320ddd00d7d51906f189069908b6100a66248e0e488467f1522ec0275` |
| `runtime/verification/cls-1118-20260911-184134/selector_evidence.json` | 2026-09-11 10:41:40Z | `fdb6087e6647fb1239da3981066ccd669507ba046135bbc875688a6672b6cddd` |
| `runtime/network-verification/4bf1a8d8d70e4069a0bfdee9e19f1a1d/report.json` | 2026-10-04 14:03:32Z | `32f874cf1657d46fd2a454cdb5ef052443002ad9e2d20997a5f7a349dd12a9fe` |
| `runtime/probe-error-verification/a14000728bab4265adcaa409859266ba/report.json` | 2026-10-05 03:59:27Z | `5ee096bcc116fa33756cec8a9f9c5e7e6c665b3216d990e5f225bd464f3ed5f3` |

源码也解释了这个缺口：`packages/inspector/browser.py:snapshot` 获取完整 `page.content()` 后返回有界简化 DOM、文本和链接；`packages/executor/http.py` 与 `playwright.py` 的 `_write_results` 保存抽取后的 `state.records`。当前这些接口/输出路径不等同于已经保存完整输入快照。

| 审计时源码 | SHA256 |
|---|---|
| `packages/inspector/browser.py` | `2437de5436e9a3f310d831986ab095bfde9022bde61247f98862ca60286f4889` |
| `packages/executor/http.py` | `1d36eb9247082b2c97f13af799497a3a6dde21d55b558d8ecdfd61fa64979c73` |
| `packages/executor/playwright.py` | `c3799142c2eef052578321b575f98278e5919c8177576ad2e0463b1304a12140` |
| `scripts/verify_crawler_agent.py` | `00f26feec11cf7ec63f3f223949c87eaca96a2947e96f1cf682aaa3f13567df6` |

## 可小规模取得的外部来源

以下数量和文件路径通过上游 README、许可证及 GitHub tree 元数据核实；未批量下载 HTML。Git commit/blob ID 是 Git 标识，不能冒充尚未下载文件的 SHA256。真正取样时还要对取得的原始压缩字节、解压 HTML 和标签分别计算 SHA256。

### 首选：WCXB 的 HTML/标注配对

[WCXB 上游](https://github.com/Murrough-Foley/web-content-extraction-benchmark) 提供 `dev/html/{id}.html.gz` ↔ `dev/ground-truth/{id}.json`，test 使用相同结构。本次 tree 核实 dev 为 1,497 对、test 为 511 对，固定 commit 为 `c039d5ee9f5a3a984a0e167e63aacd04e76e78a9`。标签含 `ground_truth.main_content`、必要片段与排除片段。它的标注由模型辅助并经过复核，不应写成全部纯人工独立标注；论文也明确包含多轮模型和人工审核。[上游格式与流程](https://github.com/Murrough-Foley/web-content-extraction-benchmark/blob/c039d5ee9f5a3a984a0e167e63aacd04e76e78a9/README.md)、[原始论文](https://arxiv.org/abs/2605.21097)

上游明确对数据声明 CC-BY-4.0，可据其条款进行内部取样与实验，保留署名、来源、许可证和变更记录；这不等于第三方原网页的任何权利都已消除。[数据许可证](https://github.com/Murrough-Foley/web-content-extraction-benchmark/blob/c039d5ee9f5a3a984a0e167e63aacd04e76e78a9/LICENSE)、[CC 官方条款说明](https://creativecommons.org/licenses/by/4.0/)

可先只取 3 对做输入/标签隔离与解压 smoke test，避免下载整套数据。它们是接入样本，不能据此挑成功案例作为最终效果证据：

| 文件对 | HTML gzip 字节数 | 标签字节数 | HTML Git blob ID |
|---|---:|---:|---|
| `dev/html/0001.html.gz` + `dev/ground-truth/0001.json` | 38,298 | 8,448 | `79d42dadde3c204cd2fee21231d4f0e86beea343` |
| `dev/html/0002.html.gz` + `dev/ground-truth/0002.json` | 77,479 | 53,929 | `1dc508ba0b5f0aa8eacdd36d69e06c4fe0640e91` |
| `dev/html/0003.html.gz` + `dev/ground-truth/0003.json` | 26,743 | 20,932 | `e22082d2b20ffaaabeae167b0a941ef1d2d3d347` |

三对合计约 226 KB。固定版本路径可直接用 `https://raw.githubusercontent.com/Murrough-Foley/web-content-extraction-benchmark/c039d5ee9f5a3a984a0e167e63aacd04e76e78a9/dev/html/0001.html.gz`，标签替换为 `dev/ground-truth/0001.json`；本次只核实树元数据与一个标签的公开格式，没有下载这些 HTML 文件。

### 备选：Scrapinghub / Zyte article-extraction-benchmark

[上游 README](https://github.com/scrapinghub/article-extraction-benchmark/blob/4a3bc979f76c0df73cb95fe272e2fc1b96f9f010/README.rst) 描述了 Splash 取得的 gzip/UTF-8 HTML 与独立 `ground-truth.json`；预测输出另在 `output/*.json`，不能混作标签。本次 tree 核实 `html/*.html.gz` 为 181 份，固定 commit `4a3bc979f76c0df73cb95fe272e2fc1b96f9f010`。输入与标签用相同 item ID 对齐，例如：

`html/042bb7b5fedab6eac7db576522b89b93904c237d344bcbe14a6a5ab7f7335856.html.gz`

标签对应 `ground-truth.json[item_id].articleBody`，可有 URL。它适合作为正文内容外部参考；README 本身不足以证明每条均由与所有抽取器无关的人工双盲标注。[仓库声明 MIT](https://github.com/scrapinghub/article-extraction-benchmark/blob/4a3bc979f76c0df73cb95fe272e2fc1b96f9f010/LICENSE)，但该软件许可证对原网页第三方内容的授权范围不能无限推定。若需要避免取回完整标签 JSON，可先选更容易单条配对和许可更明确的 WCXB。

### 工程回归：Mozilla Readability fixture

[Mozilla Readability](https://github.com/mozilla/readability) 固定 commit `ab4027a8b37669745016869a37a504727992b2ba` 的 tree 中有 130 个 `test/test-pages/*/source.html`，每例另有 `expected.html` 与 `expected-metadata.json`。例如 `001` 的输入/预期 HTML 为 12,533 / 5,912 字节；`bbc-1` 为 264,054 / 7,944 字节。仓库声明 Apache-2.0，仍应保留 notices，不推定所有第三方网页权利都被软件许可证覆盖。[许可证](https://github.com/mozilla/readability/blob/ab4027a8b37669745016869a37a504727992b2ba/LICENSE.md)

关键限制是 expected 输出的来历：仓库 [`test/generate-testcase.js`](https://github.com/mozilla/readability/blob/ab4027a8b37669745016869a37a504727992b2ba/test/generate-testcase.js) 会保存整理后的 source，并运行 Readability 生成 expected 内容与元数据。它适合回归/来源结构案例，不应不加说明地当成“独立人工正文真值”证明优于 Readability；source 也不必是原始 HTTP 字节。

## 对一个专利方向真正有用的证据

上述静态语料能补足“真实来源 HTML + 外部正文参考”，不能直接补足“同站点自然漂移前后快照”。不要把不同域名、不同文章之间的差异改称真实网站版本演化。若使用真实 HTML 人工注入错位/替换，应明确叫“真实来源上的受控变化”，原文件与改动文件分别留 hash，不能当自然 drift。

V3 最小取样建议是先用 3 对验证接入，再预先冻结一个 12–24 页的小集合；若目标是规则复用，优先包含同一出版方多个页面，另外保留原本缺少结构化来源标记的页面。保存原始 HTML、原始 URL、可取得的采集时间/方法、数据版本、许可证及标签哈希。没有响应头或独立来源链时，明确记录缺失，不补造 HTTP status、最终跳转 URL 或发布时间。

在线策略只能读取相同的原始 HTML、公开输入 URL 及由它们取得的公共证据；标签正文、`ground_truth.title`、with/without 片段均留在评分阶段。特别不能把标签中的标题注入在线 `list_title` 来帮助来源合同通过。需要真实列表标题或来源关联时，应从独立取得的公开列表页/引用链提供；没有这种证据就保持未知，而非让标签充当 oracle。

评价应同时报告：规则误复用、拒用/未证明、正文与外部标注的偏差、完整解析/候选证据取得费用。使用这些数据证明错误 memory 的适用边界与失效代价，才与可辩护专利技术效果相关；它们本身不能证明机制新颖性，也不能把已知完整投影缓存的平局包装成新效果。

## 后续转向：动态差分回放 capsule 的可取得性

本地盘点没有发现真实目标站配套的 HAR/响应体/脚本快照。因此，对固定响应的目标字段和 negative-control 字段作差分干预，目前不能直接宣称在历史新闻站 capsule 上验证过。静态 article-extraction 数据即使有完整 HTML，也不保证包含动态 API、脚本和可重放环境；本轮不继续下载它们来冒充完整回放包。

许可明确、可以按文件取得的动态测试素材确实存在。例如 Playwright 的 [`networkidle.html`](https://github.com/microsoft/playwright/blob/main/tests/assets/networkidle.html) 引用同目录脚本，其仓库提供 [Apache-2.0 许可证](https://github.com/microsoft/playwright/blob/main/LICENSE)。WPT 的 [`MutationObserver-childList.html`](https://github.com/web-platform-tests/wpt/blob/master/dom/nodes/MutationObserver-childList.html) 具有真实浏览器 DOM 修改及 testharness 脚本链，仓库提供 [BSD-3-Clause 许可证](https://github.com/web-platform-tests/wpt/blob/master/LICENSE.md)。这里确认的是公开测试源码和许可，不是已经取得并跑通全部依赖的新闻页面回放包。

这些公开测试页可用于浏览器引擎/离线依赖完整性的接入验证，但本身不提供所需“目标 JSON entity/body ↔ 正文候选”的任务标注。当前更诚实的最小实施是自编小型 HTML + renderer.js + JSON 响应 capsule，全部依赖冻结并离线执行，分别改变 target 与 foreign 字段；把它称作“真实 Chromium 中的工程受控回放可行性实验”。它能观察字段归属关系与错误复用边界，不能证明新闻正文语义正确，也不能代替自然网站效果。
