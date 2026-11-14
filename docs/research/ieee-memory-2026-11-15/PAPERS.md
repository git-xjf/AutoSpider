# 九篇 IEEE Transactions 论文阅读卡片

核验日：2026-11-15。七篇方法论文与两篇综述；题名、作者、期刊、卷页、DOI 以 [Crossref 精确核验元数据](VERIFIED_REFERENCES.json) 为准。以下“迁移”均是针对 AutoSpider 的建议，不是原论文已做的网页采集实验。

IEEE 期刊最终全文没有全部取得；公开稿与最终稿可能在作者、题名、数据集和实验上不同。本文件明确区分阅读深度，不转抄整篇论文，不把摘要中的结论扩展为未读的算法细节。

## P01｜Meta-Memory：优先读记忆更新与按需检索

**题名：** Meta-Memory for Large Language Models。Bin Liang、Cai Ke 等。IEEE Transactions on Audio, Speech and Language Processing，34:2774–2787，2026。DOI：[10.1109/TASLPRO.2026.3692286](https://doi.org/10.1109/TASLPRO.2026.3692286)。

**原机制：** M4 将增量知识分类、记忆链构建、校准/压缩更新与按需检索连接起来；记忆检索不只机械读取固定数量片段。[作者团队介绍](https://hlt.hitsz.edu.cn/info/1001/1662.htm)。

**迁移：** 将“模型自评是否有效”替换成 AutoSpider 的执行状态、QA 范围与来源证据；允许工作流在确认失效后待复验，并保留冲突与修复历史。首版不必新增模型型 Memory Manager。

**证据限制：** 期刊元数据与作者机构介绍已核；查看了公开投稿稿摘要/方法片段，未取得期刊全文，不报告未核对的数值提升。网上还可见 [ICLR 投稿版本](https://openreview.net/pdf?id=N8eKmkIxER)，不能据其投稿/撤稿标签否定已核验的期刊记录，也不能将两版实验混为一谈。

**适配度：高。** 适合更新策略，不解决一般正文真值；不能让 LLM 自信成为可执行规则入库的唯一依据。

## P02｜FinMem：借分层与寿命，保留验证门禁

**题名：** FinMem: A Performance-Enhanced LLM Trading Agent With Layered Memory and Character Design。Yangyang Yu、Haohang Li 等。IEEE Transactions on Big Data，11(6):3443–3459，2025。DOI：[10.1109/TBDATA.2025.3593370](https://doi.org/10.1109/TBDATA.2025.3593370)。

**原机制：** 分层长期记忆按时效、相关性和重要性检索；不同类型信息具有不同衰减尺度，结合工作记忆参与决策。[公开方法稿，§3.2](https://arxiv.org/html/2311.13743v2)。

**迁移：** 分开短时执行观测、任务/修复经验、长期已验证工作流。使用最近独立核验时间与可靠性辅助排序，而不是把所有旧选择器按固定 TTL 一删了之。

**证据限制：** 任务是金融交易，收益指标不是爬取正确率。公开旧稿与期刊作者表不同，参考文献使用期刊作者表。期刊在线 2025-08-04，预印本始于 2023-11。[作者公开期刊稿首页](https://suchow.io/assets/docs/yu2024finmem.pdf)。

**适配度：高。** 衰减尺度需要用网页漂移序列校准；“旧”不必然无效，“新”不必然正确。

## P03｜JARVIS-1：任务、状态、计划一同检索

**题名：** JARVIS-1: Open-World Multi-Task Agents With Memory-Augmented Multimodal Language Models。Zihao Wang、Shaofei Cai 等。IEEE Transactions on Pattern Analysis and Machine Intelligence，47(3):1894–1907，2025。DOI：[10.1109/TPAMI.2024.3511593](https://doi.org/10.1109/TPAMI.2024.3511593)。

**原机制：** 将成功轨迹中的 task、state、plan 一同存储；按任务与环境状态检索。公开稿的记忆消融比较了文本检索、推理加文本检索、推理加多模态检索。[公开稿 §3.3、§4.2.2、附录 A.3](https://arxiv.org/html/2311.05997v3)。

**迁移：** 将“环境状态”替换为当前任务字段、页面结构、传输方式和来源条件；保留可回放验证的成功流程，并让失败与它的具体版本关联。无需先训练新的多模态模型。

**证据限制：** Minecraft 成功率不能迁移成网页成功率；它保存成功经验，不是你需要的全部失败治理机制。正式卷期 2025-03，但在线 2024-12-05、预印本始于 2023 年。[出版方日期](https://ieeexplore.ieee.org/document/10778628/)。

**适配度：高。** 改“按域名推荐”为“兼容条件过滤后检索”最直接；仍要保留现有严格复用检查。

## P04｜LMAgent：快路径有用，但项目已经部分具备

**题名：** LMAgent: A Large-Scale Multimodal Agents Society for Multi-User Simulation。Yijun Liu、Wu Liu 等。IEEE Transactions on Multimedia，28:2668–2680，2026。DOI：[10.1109/TMM.2026.3651078](https://doi.org/10.1109/TMM.2026.3651078)。

**原机制：** 将简单高频行为的已有表示和重要性缓存，避免每次都调用模型压缩与评分；复杂行为仍交给模型。[公开稿 §III-B2、§IV-D1](https://arxiv.org/html/2412.09237v2)。

**论文证据：** 公开稿在 100 agents、10 epochs、5 次重复的消融中报告约 40% tokens 降低；相应表格中平均购买准确率从 72.27 到 71.99，下降 0.28 个百分点。这不是无损保证，也不是 AutoSpider 实测。

**迁移：** 将常见已确认错误的诊断结果、稳定工具合同与最小修复步骤做有条件的确定性复用。但 AutoSpider 已有六个紧凑工具与服务内工作流复用，新增收益要相对这个现状测。

**适配度：中高。** 建议复用结构化小记录，不新增十几个角色。期刊在线 2026-01-12，预印本始于 2024-12。[出版方](https://ieeexplore.ieee.org/abstract/document/11329188/authors)。

## P05｜GCAgent：事件过程记忆，谨慎处理因果与成本

**题名：** GCAgent: Long-Video Understanding via Schematic and Narrative Episodic Memory。Jeong Hun Yeo、Sangyun Chung 等。IEEE Transactions on Multimedia，28:15084–15096，2026。DOI：[10.1109/TMM.2026.3724344](https://doi.org/10.1109/TMM.2026.3724344)。

**原机制：** 将事件单元与角色/情境及时间、叙事关系组织成情节记忆，结合查询相关证据进行推理。[公开稿 §III](https://arxiv.org/html/2511.12027v1)。

**迁移：** 将探测、执行失败、选择器修改和 QA 恢复组织为过程记录，以减少下一次重新解释全部日志。关系要由执行事件标识支撑；模型推测的原因只能标成假设。

**论文证据与限制：** 其 token 表是摘要与转写长度比较：长视频 9717.7→2045.6，短视频反而 268.4→592.9；不是全部建库与推理账单。视频理解收益不证明网页来源绑定正确。

**适配度：中。** 适合有较长修复过程的任务；短任务先建复杂记忆可能增成本。期刊在线 2026-08-14，预印本为 2025-11。[出版方](https://ieeexplore.ieee.org/abstract/document/11656365/similar)。

## P06｜长期上下文架构：作为朴素强基线

**题名：** Memory-Augmented Architecture for Long-Term Context Handling in Large Language Models。Haseeb Ullah Khan Shinwari、Muhammad Usama。IEEE Transactions on Artificial Intelligence，7(10):5830–5837，2026。DOI：[10.1109/TAI.2026.3701680](https://doi.org/10.1109/TAI.2026.3701680)。

**原机制：** 使用查询与历史交互嵌入检索相关记忆，并比较 LRU 与基于近期查询相关性的裁剪。[公开稿 §II、§III-F](https://arxiv.org/html/2506.18271v1)。

**迁移：** 为任务/修复经验提供向量检索与容量控制对照。不能仅凭向量相似认定旧 XPath 能执行，不能照搬“把每次模型回答都写回”的方式污染已验证规则库。

**证据限制：** 公开稿 Gemma 2 9B 的不裁剪准确率 82.6，相关性裁剪 82.1，同时内存/延迟下降；说明需要质量与成本共同评价。期刊全文未逐页核对，公开稿不能当最终实验版本。

**适配度：中。** 优先作为基线，不以“加向量检索”本身主张新的机制。

## P07｜PGRAG：图索引相关，但不能替代执行证据

**题名：** Empowering Large Language Models to Set Up Knowledge Retrieval Indexing via Self-Learning。Simin Niu、Mengwei Wang 等。IEEE Transactions on Knowledge and Data Engineering，38(3):1710–1724，2026。DOI：[10.1109/TKDE.2025.3649907](https://doi.org/10.1109/TKDE.2025.3649907)。

**原机制：** 将文档知识组织为 mind maps，连接相关主题或一致事实，形成 pseudo-graph，并检索相关子图。[出版方摘要](https://ieeexplore.ieee.org/abstract/document/11320578)、[作者仓库](https://github.com/IAAR-Shanghai/PGRAG)。

**迁移：** 适合探索跨多次执行连接“任务—字段—规则—证据”的检索。图节点来自结构化事件即可，不必一开始对全部网页做大模型实体抽取。

**证据限制：** 论文问答与纠幻效果不是执行规则正确率。早期公开稿明确提到长文本分段与大规模 LLM 建索引成本问题；该稿题名和作者顺序也与最终元数据不同。[早期稿及 limitations](https://arxiv.org/html/2405.16933v1)。在线 2025-12-31，正式卷期 2026-03。

**适配度：有条件的中。** 先做同信息表结构对照；没有多跳需求时，引入图可能增加费用而不增加判别证据。

## P08｜终身学习路线图：用于设计评估

**题名：** Lifelong Learning of Large Language Model Based Agents: A Roadmap。Junhao Zheng、Chengming Shi 等。IEEE Transactions on Pattern Analysis and Machine Intelligence，48(5):5552–5571，2026。DOI：[10.1109/TPAMI.2025.3650546](https://doi.org/10.1109/TPAMI.2025.3650546)。

**范围：** 综述感知、记忆、行动模块的持续学习与动态适应。[出版方](https://ieeexplore.ieee.org/document/11328884/)、[作者预印本](https://arxiv.org/abs/2501.07278)、[作者资源库](https://github.com/qianlima-lab/awesome-lifelong-llm-agent)。

**迁移：** 将评估从单次静态页面扩展为“成功→变化→失败→修复→恢复”的时间序列，观察错误是否被学回、旧能力是否回退。是研究设计参考，不是一项在本项目已跑通的算法。

**阅读深度：** 核验出版记录和摘要/框架，未逐篇复核其参考文献。期刊在线 2026-01-05，正式卷期 2026-05；不能因 DOI 含 2025 就写成 2025 在线。

## P09｜自主智能体风险综述：补充可信记忆边界

**题名：** A Survey on Autonomy-Induced Security Risks in Large Model-Based Agents。Hang Su、Jun Luo 等。IEEE Transactions on Pattern Analysis and Machine Intelligence，48(10):11382–11401，2026。DOI：[10.1109/TPAMI.2026.3688650](https://doi.org/10.1109/TPAMI.2026.3688650)。

**范围：** 讨论记忆污染、工具使用和跨模块风险，以及记忆生命周期等防护方向。[出版方摘要](https://ieeexplore.ieee.org/abstract/document/11498611/)。

**迁移：** 网页文字与模型反思不能直接变成已验证规则；主控、分析、QA 转述同一结果不能被计作三份独立证据。引入经验库时保留来源与状态，并沿用现有访问策略。

**阅读深度：** 本轮核验期刊元数据和出版方摘要，没有精读全文或复现其框架。在线 2026-04-28，正式卷期 2026-10。作为综述，不给它虚构具体网页修复成功率。

## 扩展阅读的处理

不在 IEEE Transactions 清单内的论文仍可形成必要强基线，但要单列来源。尤其不要把 [LSTM-MAS 作者稿](https://arxiv.org/abs/2601.11913) 页眉的期刊模板文字当作已核实录用；本轮未得到可匹配期刊 DOI，保留为待核验扩展项。

本文没有以 IEEE 限制为理由省略已有的强工程基线：精确缓存、完整投影缓存、状态机、BM25/向量检索、同信息关系查询，都应在后续 AutoSpider 实验中比较。
