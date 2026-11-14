# v3 窄检索：网页来源依赖与受控差分绑定

核查日期：2026-10-20。本文只评价技术公开与实验区分力，不确认可申请性、授权前景、权利有效状态或不侵权。专利权项以本次实际阅读的指定国家及 A/B 文献为准；同族不等于权项一致。本次没有改生产代码，也没有将下述待实现机制写成既有项目能力。

## 1. 当前判断

“HTML、JSON-LD、API 的一致不能重复计作独立确认”是重要工程问题，但来源复制与共同上游依赖早有公开；“依赖图＋撤回证据＋按预算找新证据”不能直接作为新技术组合主张。

进一步收窄到“修改响应中某个 JSON 字段，再观察某个 DOM 候选是否变化”，仍直接面对 MUTAFLOW、Privacy Oracle 和动态污点推论专利。随机 canary、控制其他输入、重复原样执行、记录见证、未覆盖分支不作无流判断，也不能各自当作新点。下文最多保留一个待检验的窄问题，不把新命名视为区别。

## 2. 本轮拟检验机制的准确边界

以主代理提出的研究方案为对照对象，而非已完成实现：

1. 在隔离、本地、可重置的浏览器重放环境中，截获已记录的响应；定位已知目标实体的 JSON pointer，仅替换该字段值。
2. 用不在基线快照中出现、且不破坏字段类型和必要输入约束的标记值作为正向干预；记录候选 selector 对应节点的变化。
3. 在其他条件相同的重放中，改动非目标 record 的相应字段，形成负对照；另有重复不改动的基线，用于排除自发变化。
4. 若候选只随已测试目标字段变化、不随已测试非目标字段变化，可报告“该次快照与轨迹中观察到的目标字段响应绑定”。这不是全输入范围的独占依赖证明。
5. 收据可记录响应内容、脚本集合、重放状态、干预位置与值、观察节点、对照执行及结果。只给 HTTP 响应和脚本做 hash，不足以覆盖浏览器状态、服务工作线程、时钟、随机数、事件调度和未记录响应。
6. 不依赖网页自报 schema 来证明自身正确；不从响应依赖推导内容真实、目标实体识别正确或页面区域具有“正文”角色；未触发、分支改变、节点不可比时必须允许 unknown。

这组限定首先是在修复证据可信度，是否形成超出常规差分信息流测试的技术贡献，仍需强对照。

## 3. 最强论文与一手实现先例

### L1. MUTAFLOW：直接否定“修改 source → sink 改变”作为新点

Mathis、Avdiienko、Soremekun、Böhme、Zeller，*Detecting Information Flow by Mutating Input Data*，ASE 2017，263–273。作者全文：[ASE17.pdf](https://mboehme.github.io/paper/ASE17.pdf)；[大学元数据及 DOI](https://research.monash.edu/en/publications/detecting-information-flow-by-mutating-input-data)，DOI `10.1109/ASE.2017.8115639`。

已读 §II-A–F，尤其 PDF 第 3 页：每次只变更一个 source，执行基线和变体，比较 sink，输出原始值及变更后值见证；至少两次原样执行以排除不确定 sink。作者明确要求可复现，也明确未观察到流不等于无流。实现对象是 Android，非网页实体字段；但差分推断核心不能重新主张。

### L2. Privacy Oracle：受控黑盒差分与状态重置并非新增

Jung、Sheth、Greenstein、Wetherall、Maganis、Kohno，*Privacy Oracle: a System for Finding Application Leaks with Black Box Differential Testing*，CCS 2008，279–288。[作者全文](https://homes.cs.washington.edu/~yoshi/papers/PrivacyOracle/privacyoracle-ccs2008.pdf)；DOI [10.1145/1455770.1455806](https://doi.org/10.1145/1455770.1455806)。题名、作者、会议年份由全文首页核对；DOI 精确查询的 CrossRef 记录题名缩写为 *Privacy oracle*，六名作者、会议、日期与页码相符。

已读 §2、§3.1–3.2、§4.1–4.2：先重复相同输入找稳定输出，再只改变测试参数；用虚拟机快照回滚及固定交互流程控制状态。观察对象是网络字节流，不是网页正文；不过“无源码、重放、正向扰动、排除噪声”已公开。其输出对齐算法不等同于当前 selector 检查。

### L3. 来源共同上游：定义本身已有明确公开

Dong、Berti-Equille、Srivastava，*Integrating Conflicting Data: The Role of Source Dependence*，PVLDB 2(1)，2009，550–561。[VLDB 全文](https://www.vldb.org/pvldb/vol2/vldb09-pvldb47.pdf)。已读 §2–3：来源直接或传递地取自共同来源即可能依赖；基于共同错误及不同值估计复制概率，进而折扣重复支持。该模型需要概率假设，不提供本地浏览器干预证据；但“同源不算多票”不能当差异。

### L4. 撤回支持与失败依赖回溯：早于 agent memory

Doyle，*Truth Maintenance Systems for Problem Solving*，IJCAI 1977，247。[会议一页全文](https://www.ijcai.org/Proceedings/77-1/Papers/035.pdf)。已读全文：记录事实的 justification，支持变化时重判依赖结论，并作依赖导向回溯。这里只以这篇实际读到的一页论文支持该基础机制，未声称精读其引用的 MIT 完整报告。因此“删除失败证据后局部重算”本身不作为创新。

### L5. 浏览器 canary 的直接工程先例

PortSwigger 的 [DOM Invader 首发说明（2021-06-30）](https://portswigger.net/blog/introducing-dom-invader)已公开向 source 注入唯一字符串，在浏览器内检查到达的 sink、上下文和 stack trace；它还支持查看、修改及重发 JSON/对象形态的 web message。其安全 sink 目的与正文候选不同，但“唯一标记到 DOM”并非空白。

另已读 [2021.7.2 一手发布说明（2021-07-22）](https://portswigger.net/burp/releases/professional-community-2021-7-2)的 DOM Invader improvements：对每个 source 在 canary 后追加不同随机字符串，以区分到达 sink 的来源；也允许排除会破坏页面的 source。这项 per-source 标记公开来自该 release，不能错误归到 6 月首发文字。因此“不同来源用不同 sentinel”及避开 fragile source 也不作为区别。

Mozilla 的 [Bug 1887595](https://bugzilla.mozilla.org/show_bug.cgi?id=1887595)还直接讨论给 JSActor JSON 加测试键值，在 DOM 属性设置处检测 canary。此处是公开工程记录而非论文或专利；网页本次仅显示相对日期，故不填写未经精确复核的公开日，也不将其称为正式落地机制。

## 4. 专利：实际权项，而非摘要对应

下表的日期是文献元数据日期，不是当前登记簿法律结论。A 版公开日与 B 版文献日分开；除表内说明外，没有假装读过 A 版权项。

| 文献、题名 | 日期与本次阅读范围 | 与当前方案的对应与限制 |
| --- | --- | --- |
| [CN111954880B：用于动态污染流推论的方法、系统和计算机可读介质](https://patents.google.com/patent/CN111954880B/zh) | 优先权 2018-04-05；申请 2019-03-28；A 公开 2020-11-17；B 文献日 2024-06-25。实际读 B 权 1–8、9、16；未单独比较 A 权项。 | 权 1：源/汇值关系推论、执行检查、生成源汇间执行追踪并判断变换操作；权 5：变异源值并检查对汇值影响；权 6：操作关联同一请求；权 2：记录源/汇值及位置。JSON pointer→DOM 是源/汇实例化；没有调用内部操作追踪，并不消除其说明书及变异检查的公开启示。不能说权 1 单独覆盖任何黑盒差分。 |
| [EP3776314B1：Staged dynamic taint flow inference](https://patents.google.com/patent/EP3776314B1/en)（上项同族） | 申请 2019-03-28；A1 公开 2021-02-17；B1 文献日 2024-05-01。实际读 B1 权 1–8、12，及说明书 Fig. 3 的 sink/trace checks；未比较 A1 权项。 | EP 权 1 已将同一 connection/data request 的追踪操作并入独权；权 5 为源变异与汇变化；权 7 复用推论结果。说明书把未到达 sink 视为不确定，并对观察变化继续追踪检查。CN 权 1 与 EP 权 1 不可混写；也不可把该同族算成两项独立发明。 |
| [US10129285B2：End-to-end taint tracking for detection and mitigation of injection vulnerabilities in web applications](https://patents.google.com/patent/US10129285B2/en) | 申请 2016-04-27；A1 公开 2017-11-02；B2 文献日 2018-11-13。实际读 B2 权 1–8；未比较 A1 权项。 | 权 1 不只是浏览器追踪：还包含服务器请求解析、非字面 token 处理和 multipart 响应结构。权 4 将被标记响应片段传播到 DOM；权 7 列举 XHR response 等源；权 8 包含源标识及范围。它压缩“响应→DOM 来源记录”的空间，但不能忽略权 1 的组合限定来断言覆盖本方案。 |
| [US8190546B2：Dependency between sources in truth discovery](https://patents.google.com/patent/US8190546B2/en) | 申请 2009-05-15；A1 公开 2010-11-18；B2 文献日 2012-05-29。实际读 B2 权 1–11、13；未比较 A1 权项。 | 权 1 取得成对依赖与方向概率、生成依赖图、按图概率计算 truth count；权 6 对有子依赖的来源折扣。它不是浏览器干预，但直接否定“依赖图防重复确认”这一宽说法。不可将项目中的确定性同一响应判定误写成该专利的概率图算法。 |
| [US9348891B2：Online data fusion](https://patents.google.com/patent/US9348891B2/en) | 申请 2011-12-05；A1 公开 2013-06-06；B2 文献日 2016-05-24。实际读 B2 权 1–7、15–19；未比较 A1 权项。 | 权 1 在复制关系下维护答案正确性的期望/上下界概率，判断额外来源不大可能改变答案时停止；权 17 按复制关系、来源质量或覆盖排序。它压缩“依赖感知＋自适应补证＋提前停止”的组合；不等于浏览器局部字段干预。不能把强制成本预算说成该权项明文要件。 |

## 5. 要件级压缩：还剩什么，不能偷换什么

| 拟用要件 | 最近公开 | 本轮可作的保守区分 |
| --- | --- | --- |
| 多来源一致先检查共同上游 | L3；US8190546B2 | 当前拟用本地干预证明观测依赖，而不是只凭内容和统计推断复制；但干预本身又见 L1/L2。 |
| 替换 target JSON pointer，观察 selector | L1；CN111954880B 权 5；L5 | record/pointer 与 DOM candidate 是更具体的输入输出单位，尚不能仅靠换场景成立实质区别。 |
| 重置、重复基线、一次只改一个变量 | L1 §II-E；L2 §4 | 必要实验控制，不单列创新。 |
| target 改变、non-target 不改变 | 单源干预信息流矩阵可自然得到此模式 | 可形成实体绑定判据，但必须与同样逐源干预、同样预算与对照的强基线比较；不能只赢单次 canary 或字符串匹配。 |
| 收据记录 source/sink 与见证 | L1；CN111954880B 权 2；US10129285B2 权 8 | 多加 hash 是常规快照/缓存绑定；需要新机制和额外技术效果，不能把收据本身当区别。 |
| 不将未见路径推广为不存在依赖 | L1 §II-F | 动态测试的已知边界，不是新推理能力。 |
| 证据失效、局部撤回和重放 | L4；既有依赖缓存 | 若新增内容仅是依赖管理，仍属于常规组合风险。 |
| 预算内找最能区分候选的 probe | US9348891B2；主动试验的一般原理 | 只能围绕具体约束与可复核效果研究，不能以“区分式补证”命名代替算法差异。 |

“无 schema”“多智能体”“长期记忆”均不是以上公开的反例。是否落入某件现有权利要求与是否缺乏新颖性/创造性是不同问题；本文只作技术公开对照。

## 6. 最多保留的一个待检验窄问题

### H1：共渲染页面中，能否形成可靠且可拒判的 record–field–candidate 绑定判据？

问题不是“这个文本来自 API 吗”，而是在一个响应同时含目标、推荐项、列表、镜像字段、旧版本和共享模板时，能否识别候选依赖的是哪个 record 的哪个字段，并在证据不足时拒判。待检验对象是具体干预协议、可比性检查与判据的组合，不是 canary、负对照、memory、hash 或 probe 排序本身。

最低强基线应是“浏览器版逐 source mutation”：使用完全相同的 JSON pointers、候选节点、快照、重置、两次稳定性检查、目标及非目标干预、次数和费用。若两者形成相同的响应矩阵、判定和缓存复用结果，则本轮只有应用实现与可信度修复，不再另称技术创新。

必须预先设计以下否证例，而非只在成功样本上统计：

- 正文和推荐卡都引用同一 target record：二者会同时通过 target 正向／non-target 负向对照；机制不得据此选出正文。
- 只检查一个非目标 record，却遗漏另一个依赖 record：只能说“相对于已测对照未见响应”，不得声称全局专属。
- canary 触发校验、长度阈值、分支或错误页：普通输出变化不能自动视作原语义路径的绑定。
- 模板字段聚合多个 record、按排序重建列表，或 selector 绑定节点身份改变：应区分聚合依赖、对象重排与字段内容响应。
- 水合、跨响应 join、缓存、服务工作线程和定时更新：先证明重放可比；无法固定或观测的依赖进入 unknown。
- 被测字段没有被执行路径消费、文本被截断/摘要，或内容本来由另一响应提供：没有 canary 不构成不存在流的证明。

可比较指标是误绑定率、误接受率、unknown/覆盖率、同 record 多区域歧义保留率、每个可验证绑定所需的重放次数、实际 wall time；不能用“多模态证据数”或缓存命中率代替。所有判定必须由真实浏览器观察推导，不能读取 fixture 的预设 expected、origin 标签或自报 source metadata。

本轮未找到足够证据再保留第二个独立差异。“依赖证明收据＋版本绑定＋失败撤回”目前只作为实现约束和审计能力，不作第二创新方向。现有资料既不支持确认可授权，也不足以作穷尽性否定；下一步应先看 H1 在强基线下是否还存在机制和效果差异。

## 7. 检索与核验记录

这是一轮沿具体机制及引用链推进的窄检索，不是穷尽检索；未覆盖 CNIPA 全量同义词检索、审查档案和全球有效权利状态。代表性实际检索式如下，保留本轮输入原样：

```text
"browser" "JSON" "mutation" "information flow" DOM
"patent" "taint" "mutation" "source" "sink"
"canary" "response" "DOM" "taint"
site:patents.google.com/patent "JSON" "canary" "taint"
site:patents.google.com/patent "web" "extraction" "perturb" DOM
"browser" "data binding" "mutation" "provenance"
```

检索后沿引用/同族核对上述 B 权项。部分网页返回失败时重开原文或切换文献语言页面，未以摘要补全缺失权项。`US11741237B2` 是进一步追踪线索，但本次返回页面未提供可核对的 Claims 区，因此没有纳入权项覆盖结论。

采用 `nature-academic-search` 的来源分级与逐条核验方法，以及 `pdf` 的原文阅读原则；会话没有该学术技能列出的 CrossRef/arXiv MCP 工具。最初 CrossRef 直连出现连接错误和题名查询误匹配；随后对两个确定 DOI 的 REST 精确查询成功，核对了 L1/L2 的作者、会议、年份与页码，不能把前面的误匹配当命中。论文技术结论依作者/会议全文；未使用转载摘要形成技术结论。在线 PDF 文字已能读取，本次没有生成或编辑 PDF。

书目去重规则：L1 以规范化 DOI `10.1109/ase.2017.8115639` 合并作者 PDF、大学页面及 CrossRef 记录；L2 以 `10.1145/1455770.1455806` 合并作者 PDF 与 CrossRef 记录。L3/L4 未在本次核验 DOI，以题名＋首作者＋年份为唯一记录；未填猜测 DOI。L5 是一手产品/工程公开，不混计为论文。CN/EP 污染推论同族只计一个技术家族，但逐国列权项。

报告按来源简短转述、不给大段引文。结论中的“常规组合风险”是由这些公开所作的保守技术推断，不是专利法律意见。
