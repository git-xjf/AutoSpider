# 实际核验结果与改动范围

日期：2026-10-26。只有合成诊断页面和 MockTransport，没有外部站点、DNS、浏览器或模型调用。实际调用项目的草稿构建、记忆仓库、复用器、编译器、HTTP 执行器和验证器；不替换为自写抽取器。

## 1. 原来的“同值异源错替”假设

源码入口为 `build_reused_crawl`。它按位置设置 `start_urls`，按 `Operation` 设置条目／页循环上限，并保留其他值；不实现 NER→数值匹配→全局变量替换。`SiteMemoryRepository` 原样保存／读取计划，不推导参数来源图。

检查包含 HTML／Playwright 两种计划模式 × 原值 1／10／37，共 6 个参数碰撞组合；另有固定 HTTP 参数保留与变更查询触发重分析 2 例。6 个组合只运行计划复用函数，不启动浏览器。等待时间和 URL 中固定的 page_size 保持原值，条目上限按请求变化，原输入不变；固定请求的变更查询被既有检查拒绝。

**8 组检查全部通过。** 这是原假设在这些输入上的负结果，不是所有参数语义安全性的证明。不能为了展示改进而另外写一个错误的全局替换器冒充旧系统。

## 2. 独立发现的传输缺陷与修复

安装环境为 HTTPX 0.28.1。用归档的原 `HttpExecutor._request` 与当前修复方法分别执行相同 MockTransport 请求，实际结果如下：

原 URL：`https://binding.example.test/list?channel=10&page_size=10`。

| 显式 params | 原实现实际查询串 | 修复后实际查询串 |
| --- | --- | --- |
| `{}` | 空，原有参数丢失 | `channel=10&page_size=10` |
| `{"page": 2}` | `page=2` | `channel=10&page_size=10&page=2` |
| `{"channel": 37}` | `channel=37`，page_size 丢失 | `channel=37&page_size=10` |

这三种原请求中，策略检查的 URL 与实际发出的 URL 不同；修复后相同。空 `params` 的行为已由 HTTPX 2024 年发布说明公开，因此是本项目调用方式的错误，而不是库的新缺陷或本项目新颖算法。[官方发布记录](https://github.com/encode/httpx/releases/tag/0.28.0)

生产修复只在 `packages/executor/http.py`：首次请求用 `URL.copy_merge_params` 按键合并有值的显式参数；没有显式参数时保留原 URL；对合并后的实际 URL 进行策略／robots 检查并发送，不再额外传 `params` 覆盖它；后续跳转以 Location 为准，不重复注入原参数。

6 项独立回归覆盖原查询保留、增量参数、同键覆盖、重复键、303 跳转、robots 拒绝在发送前生效。该修改不解决选择器语义适用性，不宣称修复所有记忆问题。

## 3. 原假设以外：旧任务常量仍被沿用

诊断使用两个人工页面版本：主区栏目由 10 变为 37，推荐区则保留栏目 10。相同文章实体的文本和链接稳定，区域角色改变。旧任务都先经实际执行和 QA PASS，再写入新建的隔离 SQLite，读取后执行复用。

以下是 6 个诊断转移，不是 6 个独立网站；3 个错误行只是同一栏目变化下的 3 种选择器形式。评分器通过构造页的预期目标 URL 集合判断所属栏目，两个防护都看不到该评分标签。

| 情形 | 现有复用结果 | 实际 QA | 简单条件／图编码防护 |
| --- | --- | --- | --- |
| 查询未变，主区通用选择器 | 正确 | PASS | 接受／接受 |
| 栏目改变，主区通用选择器 | 正确 | PASS | 重分析／重分析 |
| 栏目改变，固定 data-channel 属性选择器 | 错误，抽到推荐区 | PASS | 重分析／重分析 |
| 栏目改变，固定 class 选择器 | 错误，抽到推荐区 | PASS | 重分析／重分析 |
| 栏目改变，带固定常量的 XPath union | 错误，抽到推荐区 | PASS | 重分析／重分析 |
| 只增加无关 tracking 查询参数 | 正确 | PASS | 重分析／重分析 |

这里没有把 10 错替为 37；问题恰好是保存规则中的旧任务相关常量没有适配或被识别为不再适用。它与既有非空误抽取问题相连，但不能因此断言在线真实任务已发生相同错误。

传输缺陷先被修复，才执行这组正式诊断，避免 URL 查询丢失混淆选择器结论。原始失败尝试保留在 `diagnostic-r1/`，仅有运行痕迹，不是有效效果报告。`diagnostic-r2/` 为中间诊断；完善输入不变性守卫与页面哈希后，`final-r3/` 为正式依据。

## 4. 图谱与其他方法的实际对照

简单基线：查询串变化时，要求重新分析。图对照把同一前提表示为查询节点到工作流的“适用性未证明”关系；这是不确定关系，不是已经发现的因果边。两个方法使用相同公开输入。

| 判据 | 正确接受 | 错误接受 | 安全转移被拒绝 | 要求重分析 |
| --- | ---: | ---: | ---: | ---: |
| 当前复用 | 3 | 3 | 0 | 0 |
| 简单查询门槛 | 1 | 0 | 2 | 5 |
| 相同信息的图编码 | 1 | 0 | 2 | 5 |

表内防护是对同一组实际执行结果计算的准入决策，没有独立再运行全部抓取，不声称三种方法的时间／HTTP 费用比较。正式诊断实际共 12 次模拟 HTTP（6 个源任务＋6 个复用任务）；原／修复后传输对照另有 6 次模拟请求。外部 HTTP 和 LLM 调用均为 0。

图与表逐项等价，故没有图谱额外收益证据。保守门槛也不是完整安全证明：它没有检查所有正文角色或相同查询下的变化。本轮没有将其部署到生产复用器，没有用 coverage 下降掩盖错误率。

## 5. 可复现性与冻结证据

```powershell
python scripts/probe_workflow_parameter_binding.py --output-dir runtime/experiments/patent-parameter-binding-20261026/review-new-name
python scripts/probe_http_query_preservation.py --output runtime/experiments/patent-parameter-binding-20261026/transport-review-new-name.json
python -m pytest tests/unit/test_http_query_binding.py tests/research/test_workflow_parameter_binding.py -q -o addopts=''
```

新目录／新文件独占创建，拒绝覆盖。正式 report 保存 11 个生产／研究源码哈希、运行前后一致标志、逐例页面哈希、执行结果哈希和源计划不变性检查。

| 对象 | SHA256 |
| --- | --- |
| 原 HTTP 源码归档 `http-before-query-fix.py` | `1d36eb9247082b2c97f13af799497a3a6dde21d55b558d8ecdfd61fa64979c73` |
| 修复后 HTTP 源码 | `ffae762a96cf0030cb08f96fb34267b17897dc105e4bde174f1754cc20cae465` |
| `final-r3/report.json` | `ab5b1cd3d8b2ed151d4065b7b6a5e7c6ed4187b7ccdeb6f859a1a97ef110b4fe` |
| r3 诊断实现及 `probe-source.py` | `f18f9137074458655b8e86291ee3a1647c74e08d3b0b48f2a88f02ab4c951762` |
| r3 CLI 及 `runner-source.py` | `5467113876148f25b0719d2412a0950c2c9c69b68c27cfd323613ca10720857f` |
| `transport-before-after.json` | `213cd42c8ef71c2bf4be633eddb2d0c8410f79b0f9ed48f335055f7f797db1a5` |

新增针对性测试 **15 passed / 1.48 秒**。全项目最终回归为 **700 passed、1 warning / 64.22 秒**；警告是 Pydantic Settings 对 `lifespan` 未解析前向引用的提示。测试命令如下，不把测试数量当作专利技术效果：

```powershell
D:\conda_envs\mytorch\python.exe -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=.research/pytest-parameter-binding-full-r1
```

用户 `data/site_memory.db` 在检查前后及全项目回归后均为 `b6bb171ea460c4a86e73a651269e42a382279185fa2ab6c6b44e7508fa26e504`；新诊断记忆仅写在本轮证据目录的 `isolated-memory.db`，测试使用临时目录。未读取真实运行正文、凭证日志或进行网站重采样。既有冻结 v2/v3 及新闻页证据未覆盖。
