# 记忆系统专利研发：第二版交付

2026-10-19。本轮已完成隔离原型、冻结对照实验、独立测试和技术交底候选；**尚未完成一个已有充分创新依据的可申请发明**。

结果：来源/版本/状态绑定修复了旧版错误泛化；但与同信息完整投影缓存在全部255个任务/预算组合中持平。元数据自洽但正文错误的反例仍会放行。当前不建议仅凭此机制提交发明申请。

## 建议阅读顺序

1. [RESULTS.md](RESULTS.md)：实际数字、反例、成本、测试和申请决策。
2. [TECHNICAL_DISCLOSURE_V2.md](TECHNICAL_DISCLOSURE_V2.md)：已实现方案、窄适用范围、尚待研发的问题。
3. [PRIOR_ART_AND_CLAIMS.md](PRIOR_ART_AND_CLAIMS.md)：补充3件依赖缓存/预算测试先例，逐项权项风险及候选骨架。
4. [IMPLEMENTATION.md](IMPLEMENTATION.md)：代码、数据结构、状态和复现。
5. [PROTOCOL.md](PROTOCOL.md) / [FIXTURES.md](FIXTURES.md)：冻结输入、评分边界和公平性。

## 代码与证据入口

- [core.py](../../../research/memory_v2/core.py)：研究用核心，不接入生产。
- [fixtures.py](../../../research/memory_v2/fixtures.py)：9开发＋15工程留出场景。
- [benchmark_memory_applicability_v2.py](../../../scripts/benchmark_memory_applicability_v2.py)：冻结与实验CLI。
- [test_memory_applicability_v2.py](../../../tests/research/test_memory_applicability_v2.py)：62项独立测试。
- [正式结果JSON](../../../runtime/experiments/patent-memory-v2-20261019/pilot-aligned.json)：255行任务/预算组合及1785次策略评价。

既有[论文集](../memory-2026-10-19/PAPERS.md)、[专利集](../memory-2026-10-19/PATENT_LANDSCAPE.md)和[第一版负结果](../memory-2026-10-19/EVIDENCE.md)保留。此次窄检索将动态依赖缓存、局部失效和有限预算测试选择纳入对照，因此没有把常规缓存组合包装成新记忆算法。

全项目实测562项通过，1项既有依赖警告。生产采集/记忆模块未接入此原型，已有用户数据库不作迁移或修改；不要据此认为生产质量缺口已经上线修复。
