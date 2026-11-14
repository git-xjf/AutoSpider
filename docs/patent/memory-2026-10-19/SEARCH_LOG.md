# 检索与来源核验记录

日期：2026-10-19；范围：AutoSpider站点规则/工作流的外部程序记忆、适用性、失效、部分复用、wrapper维护及局部复验。不是硬件内存或一般聊天摘要的广泛综述。

## 路由与工具

使用nature-academic-search的来源分级、去重和引用核查流程。当前没有可调用的专用arXiv/Crossref检索MCP，改为网页检索发现、arXiv原站元数据/HTML全文和Crossref官方DOI接口复核。专利以Google Patents托管的原始公开/授权文本为核查入口，重点阅读权项，未完成官方登记簿法律状态审计。

## 论文实际检索与追踪

本轮使用的关键词包括以下检索式，限制到arXiv或原出版机构：

```text
"procedural memory" "invalidation" agent
"web" "memory" "drift" agent selective verification
"agent memory" "counterexample" applicability
"wrapper" "maintenance" "verification" "web" extraction
ReasoningBank Scaling Agent Self Evolving Memory 2509
ExpeL LLM Agents Experiential Learning 2308.10144
```

同时按既往相关线索直接复核arXiv ID：2409.07429、2508.06433、2602.01869、2609.03340、2609.00243、2609.33517、2606.23127、2608.21867、2608.10502、2509.25140、2308.10144、1106.4872。按ID去重，版本更新不另计篇数。

对这些记录提取原站 `citation_title`、作者、提交/更新日期及submission history，保存确切版本HTML链接及内容hash。对L12以 `https://api.crossref.org/works/10.1613/jair.1145` 交叉核对原出版年份与作者顺序，避免将2011年镜像上传当作2003年原论文发表日期。

## 专利筛选

检索围绕程序记忆、任务结果/中间链路复用、历史工作流、来源有效性、失败工具路径修复、网页包装器维护、代表页补样、依赖缓存失效等概念，并追踪公开页面中的同族A/B版本。10个正式条目的申请日、首次公开日、所读版本日期和权项定位均在[PATENT_LANDSCAPE.md](PATENT_LANDSCAPE.md)列出。没有把A摘要当作B授权权1；例如P2的A权6–8在B中对应权4–6。

检索命中后的主要保留依据为：是否直接公开候选的条件复用、部分失效、局部修复、证据选择或记忆更新控制。传统wrapper和缓存专利也纳入，避免把“在agent中使用”误当作新的技术原理。未能读取原始权项的CN122242560A排除出正式清单，未从二手摘要作结论。

## 排除与未覆盖范围

同词异义的生物记忆、CPU/虚拟内存、无关强化学习任务、只有博客转述而无可复核原文的条目不作核心证据。MemGuard等新论文的效果是作者报告，未在本项目复现，不计为本项目收益。

本轮筛选形成12篇论文与10件专利的定向资料库，不代表全球穷尽检索。尚需沿最接近文献的引用/被引及分类扩展主动测试选择、程序适用域学习、wrapper维护、规则合并回归和故障诊断的组合公开。公开前未披露的申请无法由本轮检索排除；专利同族、审查意见、年费与具体地域实施判断应另行核验。
