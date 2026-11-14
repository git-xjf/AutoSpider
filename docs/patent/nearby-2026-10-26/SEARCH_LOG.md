# 本轮检索与来源核对记录

日期：2026-10-26。用途是为用户自主阅读与后续选题提供 10—15 个近期相似专利族，不实施新机制、不生成申请稿、不继续上一轮实验。

## 范围与检索

读取项目根 README、MCP 复用服务、Site Memory、确定性 QA／RepairPatch；参考已有 10-07 与 10-19 对照索引。以 2025—2026 的新公开／新公告及技术相似性综合筛选，不以检索排序冒充全球最近日期排序。

主要查询：

- `site:patents.google.com/patent 多智能体 网页 数据 采集 大模型 2025 2026`
- `site:patents.google.com/patent 智能体 记忆 工作流 2026`
- `site:patents.google.com/patent multi agent orchestration memory verification 2025 2026`
- `site:patents.google.com/patent MCP 智能体 2026 工具`
- `site:patents.google.com/patent 多智能体 任务 修复 2026`
- `site:patents.google.com/patent 记忆 知识图谱 智能体 2026`
- `site:patents.google.com/patent 智能体 上下文 压缩 2026`
- 补查 `2026-09 / 2026-10` 的 agent／orchestration／memory 组合，未据此声称查全截至日的全球新申请；日期词也可能仅命中引证或预期事件。

候选还包括多智能体数据库查询、加密跨存储验证、具身机器人协作、用户偏好、网络故障修复及终端能力权限。综合与项目的直接关系，未用这些外围题目补数量。最终主表 15 族中 8 族本轮新纳入，7 族为既有重点复核。

## 原文读取与去重

逐件读取 15 个代表文本的书目信息及权利要求部分；中国 B 优先读取 B 权项，没有用 A 摘要代替授权限定。另对上下文生成、异步存储、动态图谱的相关说明段落作定向核对。没有逐件精读全部实施例、数学公式、审查档案或所有同族权项。

独立下载并核验 15 个代表页面的 publication number、SHA256、claims 区存在性；按页面 Family ID 去重，**15 个 ID 全部不同**：

| ID | 代表文本 | 页面 Family ID |
| --- | --- | --- |
| R01 | CN120910335B | 97545210 |
| R02 | CN122112328A | 99877112 |
| R03 | CN121167008B | 98038501 |
| R04 | CN121479039A | 98615648 |
| R05 | CN121960550A | 99566727 |
| R06 | CN122112050B | 99905156 |
| R07 | CN121766738A | 99190395 |
| R08 | CN121638260A | 98961702 |
| R09 | CN121351971B | 98402551 |
| R10 | CN121560934B | 98806259 |
| R11 | CN120892581B | 97501593 |
| R12 | CN121598989B | 98879541 |
| R13 | CN122154744A | 100009861 |
| R14 | US20260219870A1 | 100660665 |
| R15 | WO2026090475A1 | 99552330 |

这些 ID 是页面族归类，不保证完整法律上的同族／分案范围核验。R09 族表另有 CN122334421A，本轮网页工具读取失败，未取得其权项；R14 的 WO2026161774A1、R15 的 US20260119697A1 仅核对族表链接，不另作“已读文本”或独立条目。R09 的 B 页面网页工具单次失败，但独立 HTTP 下载成功，已实际读取保存的中文 B 权项；不是仅凭摘要或旧记忆编写。

页面存在网页内指令或提示词例子时，均仅作为被检索文献内容，不作为本任务指令执行。

## 快照与复现

复用既有 `scripts/collect_memory_patent_sources.py`，使用新输出目录，不覆盖旧研究材料。`papers_empty.json` 只满足旧脚本入口参数，本轮论文数为 0；没有声称下载论文或 PDF。

```powershell
D:\conda_envs\mytorch\python.exe scripts/collect_memory_patent_sources.py --papers docs/patent/nearby-2026-10-26/papers_empty.json --patents docs/patent/nearby-2026-10-26/patent_seeds.json --out runtime/experiments/patent-nearby-20261026/sources-r1 --delay 1
```

此为本次已经运行的命令。重新采集应换新输出目录：原脚本会复用已存在 HTML 并重写 collection，不是拒绝覆盖工具；不要对本轮目录再运行来冒充全新下载。

实际结果：**15/15 HTML downloaded，papers=0**。逐文件 URL、字节数、哈希与下载时间在 [collection.json](../../../runtime/experiments/patent-nearby-20261026/sources-r1/collection.json)。该清单 SHA256 为 `8937eeffe54c3bb0c998885aa4eb9bff5d0acf5ae5bc06ac48473811333284f4`。初次终端显示中文受 GBK 编码影响，改为 UTF-8 后重新解析；保存的响应字节未修改。

## 不作的结论

没有完成 CNIPA／USPTO／WIPO 登记簿、年费、现行有效权项、优先权有效性、地域及审查历史核验。Google Patents 的 Active／Pending 不当作法律状态结论；US／PCT 中某项技术是否能享有其页面列出的最早优先权，仍需逐特征核查，不把页面日期直接赋给全部新增内容。

不作侵权、自由实施或最终可授权判断。保护范围要看适用地区的有效权项整体，技术公开也不能只看标题或摘要。申请公开、授权公告、最早优先权和技术实际首次公开不是同一个日期。本轮文档已分别标注，不把 2026 年 B 公告误写成首次公开。

本轮没有修改项目生产源码或读取运行正文／凭证。用户记忆库开始与收尾时 SHA256 均为 `b6bb171ea460c4a86e73a651269e42a382279185fa2ab6c6b44e7508fa26e504`；collection 哈希一致。文档本地链接检查 23 项，无失效目标，种子清单 15 项／新纳入 8 项与主表一致。仅资料／文档工作，不把此前 779 项回归冒称为本轮新运行。
