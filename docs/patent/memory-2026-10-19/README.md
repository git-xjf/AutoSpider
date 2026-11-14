# AutoSpider 记忆系统专利立项资料包

日期：2026-10-19。目标是筛选并推进**一件专利**，不完成论文，不把论文数量或系统模块数量当作创新证据。

后续更新：已完成[第二版隔离原型与正式对照](../memory-v2-2026-10-19/README.md)。与同信息完整投影缓存255个任务/预算组合全部持平，仍有合同外正文错误；[最新结果与申请决策](../memory-v2-2026-10-19/RESULTS.md)为准。下文保留第一轮立项与先导记录，不覆盖历史负结果。

2026-10-20继续研发见[v3交付](../memory-v3-2026-10-20/README.md)：真实Chromium受控差分回放、近似公开及数据审计均已完成，仍未建立新增专利核心；没有将旧记录改写为成功结论。

本轮收集并核验12篇相关论文记录、10件专利文本；缓存11份论文HTML全文、12份arXiv原始记录、10份专利HTML及1份经典论文的Crossref出版元数据。另提供BibTeX。L12经典论文没有HTML全文，已明确限制核查范围。原始来源及文件SHA-256可追溯。

## 先看结论

推荐以**“基于反例约束的网页采集规则记忆复用与局部复验”**为研发主线。研究问题是：网站没有可靠变更通知、同一路由又存在不同模板/来源时，怎样用有限核验成本判定哪些字段规则可以继续复用，哪些必须保持待核验，同时保留未失效规则。

这一方向有实际问题基础，但**当前先导尚未证明候选优于强基线，不建议直接提交宽泛记忆专利**。本轮的价值包括找到近似公开、复现当前缺口，以及排除一次表面有效但会错误泛化的候选。继续研发应解决这些反例，而不是更换名称。

三个方向：

| 顺序 | 方向 | 当前决策 |
|---|---|---|
| 1 | 字段规则的反例约束作用域与区分式复验 | 主线；已有原型与正反结果，下一步补足来源证据与拒用边界 |
| 2 | 故障原因复验与分通道记忆状态 | 配套部件或备选；区分暂时不可访问与规则失效，避免错误失效传播 |
| 3 | 记忆合并前的反例差分验证 | 备选；先有多版本证据与压缩需求，再检验是否有额外效果 |

## 阅读路径

1. [DIRECTIONS.md：三个方向的研究问题、机制、实施路径和停止条件](DIRECTIONS.md)
2. [EVIDENCE.md：真实缺口、9情形×5策略先导与下一步取证门槛](EVIDENCE.md)
3. [PATENT_LANDSCAPE.md：10件专利的权项定位和逐项压缩范围](PATENT_LANDSCAPE.md)
4. [PAPERS.md：12篇论文的原文、版本、关键机制及对候选的约束](PAPERS.md)
5. [PROJECT_GAPS.md：项目当前能力、缺口及源码依据](PROJECT_GAPS.md)
6. [SEARCH_LOG.md：检索方法、筛选和复核边界](SEARCH_LOG.md)

## 资料与实验文件

- [原文采集索引与哈希](../../../runtime/experiments/patent-memory-20261019/sources/collection.json)
- [BibTeX文献库](../../../runtime/experiments/patent-memory-20261019/sources/references.bib)
- [当前系统缺口结果](../../../runtime/experiments/patent-memory-20261019/current_gaps.json)
- [候选/基线完整结果](../../../runtime/experiments/patent-memory-20261019/pilot.json)
- [论文种子与主题标签](paper_seeds.json)、[专利结构化索引](sources_patents.json)

全文缓存位于 `runtime/experiments/patent-memory-20261019/sources/papers/` 与 `sources/patents/`。原始HTML可能需要在线样式资源才能完整显示，但文本原件已经本地保存。收藏记录不表示所有全文都经过逐段精读；核查深度与方法定位在对应清单中说明。

## 复现

在仓库根目录执行：

```powershell
D:\conda_envs\mytorch\python.exe scripts\collect_memory_patent_sources.py --papers docs\patent\memory-2026-10-19\paper_seeds.json --patents docs\patent\memory-2026-10-19\sources_patents.json --out runtime\experiments\patent-memory-20261019\sources
D:\conda_envs\mytorch\python.exe scripts\probe_memory_patent_gaps.py --output runtime\experiments\patent-memory-20261019\current_gaps.json
D:\conda_envs\mytorch\python.exe scripts\benchmark_memory_applicability.py --output runtime\experiments\patent-memory-20261019\pilot.json
```

采集器复用已有快照，避免原站后续更新改变本次证据；新检索日期应使用新输出目录。诊断/先导脚本只操作本轮输出或临时库，没有改生产记忆库、现有任务和旧交底文件。相关现有测试本轮实际为42项通过。

本包是专利研发立项与技术对照材料，尚不是完整申报书或授权结论。先将候选落实并在强对照下获得额外技术效果，再按具体可执行差异收窄交底；论文公开也属于需要考虑的现有技术，不能只查其他专利。
