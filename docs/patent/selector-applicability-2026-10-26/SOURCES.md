# 本轮近例核查与检索边界

核查日期：2026-10-26。使用 `nature-academic-search` 的来源分级、去重与核验流程；本环境没有其列出的 arXiv／CrossRef MCP，转用 arXiv 原始摘要页、作者机构出版记录和公开专利文本。结果不是穷尽检索，没有查完中国同族、审查档案、法律状态或实施自由。

检索词围绕 `web wrapper repair verification`、`metamorphic testing web extraction`、`XPath locator repair patent`、`multi-locators robustness`、`similarity-based web element localization`；网页检索中的 ResearchGate、聚合站等只用来发现题名，不用作技术结论来源。以 DOI／题名去重，预印本与期刊扩展版不计成两个独立方法。

## 论文：实际读取到什么

| 原始／机构来源 | 元数据与已确认内容 | 本轮使用边界 |
| --- | --- | --- |
| [Using Multi-Locators to Increase the Robustness of Web Test Cases](https://portal.fis.tum.de/en/publications/using-multi-locators-to-increase-the-robustness-of-web-test-cases/) | Maurizio Leotta、Andrea Stocco、Filippo Ricca、Paolo Tonella；ICST 2015；DOI `10.1109/ICST.2015.7102611`。机构摘要明确多个算法生成候选定位器、加权投票，目的为抗页面演化。 | 读取机构元数据与摘要；未复现其完整候选生成、加权算法或数据集。本轮双定位器一致性只是参考对照，不冒称该论文复现结果。 |
| [Similarity-based web element localization for robust test automation，arXiv 2208.00677](https://arxiv.org/abs/2208.00677)；[2023 期刊版机构记录](https://research.chalmers.se/en/publication/536319) | Michel Nass、Emil Alégroth、Robert Feldt、Maurizio Leotta、Filippo Ricca；2022 预印本，2023 ACM TOSEM 32(3), 75，期刊 DOI `10.1145/3571855`。多属性定位参数的加权相似性用于元素定位。 | 读取两版原始／机构摘要与元数据，未精读或运行完整算法。两版的样本规模不同，不混用其效果数字，也不把本轮构造实验写成胜过 Similo。 |
| [REST-ler: Automatic Intelligent REST API Fuzzing](https://www.microsoft.com/en-us/research/publication/rest-ler-automatic-intelligent-rest-api-fuzzing/) | Vaggelis Atlidakis、Patrice Godefroid、Marina Polishchuk；MSR-TR-2018-11，2018-04。接口依赖和执行反馈用于请求序列生成。 | 读取机构元数据／摘要，提供“依赖＋反馈”有较早先例的背景，不声称它解决了本轮选择器语义问题。 |

## 专利：权项限定不能省略

| 公开文本 | 本轮实际核对 | 对当前方案的意义 |
| --- | --- | --- |
| [US7440951B2：Method for automatic wrapper repair](https://patents.google.com/patent/US7440951B2/en)，B 文献日 2008-10-21；另有 A 文献 US20060074999A1，公开 2006-04-06。 | 摘要、相关说明和权 1—3。权 1 不仅说“修复”，还限定由旧 wrapper 规则处理字符串、内容分类器检验标签及阈值判定；从属项包含标签恢复。 | 自动维护网页抽取规则不是新问题。但本轮没有内容分类器等相同限定，不能仅凭标题断言完全覆盖或侵权，也不能把“不是同一权项”当作创造性已成立。 |
| [JP2018092361A：Test script correction device and test script correction program](https://patents.google.com/patent/JP2018092361A/en)，公开 2018-06-14；页面列出 B 同族 JP6645955B2。 | 日／英对照页面的摘要和权项重点段落。权 1 包括旧屏幕操作顺序／识别信息、保存对象图像及新屏幕图像比较修正；后续项涉及多指标和权重。 | 定位器修复、多特征识别已有相关公开。本轮不使用图像，不据此作完整相同或不侵权结论；未把 A 的翻译当作 B 授权范围核验。 |
| [CN121351971B 及既有逐项对照](../parameter-binding-2026-10-26/PRIOR_ART_AND_DECISION.md) | 沿用上一轮权 1／7 的读取和原“数值匹配泛化”假设核查。本轮不把它冒称新的全面中文检索。 | 图式记忆与经验泛化已有近例；当前 AutoSpider 没实现该数值泛化器，不能拿自造弱实现充当旧技术。 |

Google Patents 是公开文本的访问渠道，不代表已完成登记簿或中国同族有效权项核查。任何申请与实施范围判断仍需代理师完整检索及中文权项对照。

## 申请判断依据

[国家知识产权局 2025 年《专利审查指南》修改解读](https://www.cnipa.gov.cn/art/2025/12/4/art_66_202935.html)明确，相关修改自 2026-01-01 施行，并强调对技术问题无贡献的特征通常不能带来创造性。本轮据此不将图式存储、增加节点或将一般检查改称“记忆证书”视为技术创新证据。实验改善是技术效果的材料，不自动等于新颖性／创造性成立。

排除与失败：`arXiv:2208.01244` 核对后为互补约束优化论文，与本题无关，排除；`US11093319B2` 在本轮工具中全文不可读，未据其标题／引证关系推断权项内容。未获取的全文、未实现的论文算法均不计入已完成强复现比较。
