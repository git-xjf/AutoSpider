# Memory 研发的项目缺口与三个可执行研究问题

审查日期：2026-10-19（Asia/Shanghai）。范围：当前工作区的站点记忆、工作流复用、记录验证和执行故障回执。本轮只读核查生产实现；新增本文、隔离诊断脚本及其 JSON 结果，未改生产代码、任务、草稿或用户数据库。本文识别项目研究问题，不代表完成专利新颖性检索或已证明候选控制机制有效。

## 1. 结论与现有基础

最值得切入的真实问题是：一次复用失败后，系统缺少证据来判断“暂时无法访问”“特定模板中的一个字段失效”“规则提取了非空错误内容”；因此也无法可靠决定记忆应停用哪一部分、保留哪一部分、下一次取什么证据。增加失败计数可以修复重复推荐，但不能回答上述问题。

当前已经实现的基础及其边界如下。行号对应本次审查时的文件版本。

| 现有实现 | 源码证据 | 能证明的范围 |
|---|---|---|
| 按站点保存页面指纹、字段选择器、完整工作流 | [repository.py:25](../../../packages/site_memory/repository.py#L25)、[repository.py:36](../../../packages/site_memory/repository.py#L36)、[repository.py:49](../../../packages/site_memory/repository.py#L49) | 有规则记忆，尚无字段×模板的有效性状态及完整证据绑定 |
| 选择器成功次数、失败次数列 | [repository.py:44](../../../packages/site_memory/repository.py#L44)、[repository.py:45](../../../packages/site_memory/repository.py#L45) | 成功提交会更新成功次数；失败次数列存在，但当前代码没有更新路径 |
| 工作流按 `success_rate` 排名 | [repository.py:141](../../../packages/site_memory/repository.py#L141)、[repository.py:150](../../../packages/site_memory/repository.py#L150) | 该值在提交时被设为本次 `validation_score`，不是复用历史的经验成功率；见 [repository.py:245](../../../packages/site_memory/repository.py#L245)、[repository.py:257](../../../packages/site_memory/repository.py#L257) |
| 复用时限制源路由、API/固定请求参数变更及既有字段 | [reuse.py:50](../../../packages/crawl_core/reuse.py#L50)、[reuse.py:59](../../../packages/crawl_core/reuse.py#L59)、[reuse.py:65](../../../packages/crawl_core/reuse.py#L65)、[reuse.py:96](../../../packages/crawl_core/reuse.py#L96) | 防止明显不兼容复用；同一路由改版或同路由异模板仍可能通过入口检查 |
| 复用工作流执行样本验证，再执行最终采集 | [server.py:549](../../../services/easyspider_mcp/server.py#L549)、[server.py:593](../../../services/easyspider_mcp/server.py#L593)、[server.py:605](../../../services/easyspider_mcp/server.py#L605)、[server.py:611](../../../services/easyspider_mcp/server.py#L611) | 有运行验证；失败返回 `needs_reanalysis`，没有把失败反馈到选择器或工作流的可复用状态 |
| 按 URL 路由形状检查每个必需字段空值率 | [coverage.py:42](../../../packages/validator/coverage.py#L42)、[coverage.py:153](../../../packages/validator/coverage.py#L153)、[coverage.py:186](../../../packages/validator/coverage.py#L186) | 可发现已观察到的、独立路由中的高空值字段；路由形状不是 DOM 模板身份 |
| 输出实际失败 URL、字段、样本和字符预算 | [coverage.py:109](../../../packages/validator/coverage.py#L109)、[coverage.py:213](../../../packages/validator/coverage.py#L213) | 已有补证指令；尚无将该指令变成服务端受控复验、累计预算和证据写回的接口 |
| 模型回执有总字节裁剪 | [server.py:84](../../../services/easyspider_mcp/server.py#L84) | 回执默认不超过 16,000 UTF-8 字节；这不等于实际复验请求预算或模型累计输入预算已经受控 |
| 自动草稿在 full PASS 且输出记录均已验证时提交记忆 | [server.py:768](../../../services/easyspider_mcp/server.py#L768)、[server.py:778](../../../services/easyspider_mcp/server.py#L778) | 有自动提交门禁；PASS 本身仍受现有空值和弱语义检查的能力限制 |

## 2. 五个需要正面处理的缺口

### G1：旧规则复用失败后，仍可被再次推荐

`memory_lookup_site` 直接推荐查询结果中的第一个工作流，见 [server.py:1001](../../../services/easyspider_mcp/server.py#L1001)。复用入口检查过去成功时间和存储分数，见 [server.py:568](../../../services/easyspider_mcp/server.py#L568)。失败返回路径没有改变这些记录。全仓库 Python 源码的 `record_failure(` 检索只有 [repository.py:263](../../../packages/site_memory/repository.py#L263) 的定义，没有调用点。

数据库已经有 `failures.workflow_id` 列，见 [repository.py:75](../../../packages/site_memory/repository.py#L75)，但 `record_failure` 的参数和 INSERT 均未绑定工作流、执行、规则节点、字段或路由，见 [repository.py:263](../../../packages/site_memory/repository.py#L263)、[repository.py:283](../../../packages/site_memory/repository.py#L283)。即使把该函数接到失败路径，也不足以安全地支持选择性失效。

应先作为普通工程修复处理的内容：失败写回、正确维护经验成功率、避免无限重复推荐、绑定执行 ID、按原因计数。它们是研究实验的必要底座，不能单凭这些变化主张强创新。

### G2：临时网络故障不能被当作选择器失效

直接 HTTP 执行器在 [http.py:438](../../../packages/executor/http.py#L438) 调用 `raise_for_status()`；请求超时、HTTP 状态错误和选择器异常最终都经 [http.py:105](../../../packages/executor/http.py#L105) 持久化为失败及异常类型字符串。服务端对 `PolicyViolation` 已有专门阻断分支，见 [server.py:138](../../../services/easyspider_mcp/server.py#L138)、[server.py:147](../../../services/easyspider_mcp/server.py#L147)，但没有对应的“网络原因不足以改变规则有效性”记忆控制。HTTP 探查工具中针对状态码的重试提示（[server.py:292](../../../services/easyspider_mcp/server.py#L292)）不能当作复用记忆已完成故障归因的证据。

当前代码并未自动失效选择器，所以不能描述成“已经发生误失效”；真实风险是下一步若用任意失败直接扣规则分或停用工作流，就会引入误失效。故障原因至少应区分策略阻断、暂时不可访问、解析/选择器失配、匹配为空、字段内容不符和证据不足。状态码分类属于常规工程；证据不足时选择怎样的少量复验、何时足以改变何种范围的记忆，才是候选控制问题。

### G3：同一路由包含不同模板，路由门禁仍会漏报

`route_signature` 只使用主机名、归一化路径和查询键，查询值不进入签名，见 [coverage.py:42](../../../packages/validator/coverage.py#L42)、[coverage.py:57](../../../packages/validator/coverage.py#L57)。分层键没有实际 DOM 模板信息，见 [coverage.py:153](../../../packages/validator/coverage.py#L153)。只在空值率**大于** 0.2 时失败，见 [coverage.py:186](../../../packages/validator/coverage.py#L186)。因此十个 `/news/{n}` 页面中两个使用另一模板、旧选择器匹配空占位时，整个路由仍为一层且恰好通过阈值。

本轮使用现有 `_spec()` 与纯内存记录实际复现：十条均为 `/news/{n}`，后两条正文置空；结果为 `status=PASS`、`strata=1`、`content_null_rate=0.2`、`failed_pairs=0`。没有访问网络或修改数据库。该结果也说明上轮不同 `/news/` 与 `/special/` 路由的成功回归不能外推到同路由异模板。

现有页面指纹不能直接填补此缺口。指纹由标签、类和部分语义属性的计数摘要构成，见 [fingerprint.py:11](../../../packages/inspector/fingerprint.py#L11)；它不是字段所在局部 DOM 的身份，并可能对无关布局变化敏感、对元素相对位置变化不敏感。比较函数仅返回相等 1 或不等 0，见 [similarity.py:4](../../../packages/site_memory/similarity.py#L4)。实际复用路径也没有获取并核验当前详情页指纹，见 [server.py:568](../../../services/easyspider_mcp/server.py#L568)。

### G4：正文非空但提取了错误区域，会绕过当前校验

现有语义检查仅对 URL、日期执行类型检查；普通字符串主要检查批量值是否过度相同，见 [semantic.py:39](../../../packages/validator/semantic.py#L39)、[semantic.py:59](../../../packages/validator/semantic.py#L59)。唯一性高不代表是正确字段。完整率按键是否存在计算，见 [metrics.py:24](../../../packages/validator/metrics.py#L24)、[metrics.py:30](../../../packages/validator/metrics.py#L30)；重复率按整条记录计算，见 [metrics.py:27](../../../packages/validator/metrics.py#L27)。

本轮把十条正文全部换为各不相同的 `Distinct related-links text {i}`，实际得到 `status=PASS`、`semantic_score=1.0`、`failed_pairs=0`。该实验只证明校验器无法区分这组错误字符串；未声称真实网站误提率为 100%。

直接加“最少正文长度”属于弱启发式：足够长的侧栏仍会通过，真实短正文可能被误判。研发需要记录字段角色及来源区域的可核验证据，并承认没有独立依据时不能仅凭两个非空候选的差异判定谁正确。

### G5：失败应停用的范围与成功规则应保留的范围尚未建模

字段选择已经支持删除不请求字段的 EXTRACT，见 [reuse.py:118](../../../packages/crawl_core/reuse.py#L118)、[reuse.py:121](../../../packages/crawl_core/reuse.py#L121)。但这只是字段投影，不是依据失败证据更新记忆，也不会据此完整裁剪详情导航等上游操作。

例如 Wbody 提取 `title/url/content`，Wmeta 提取 `title/url/date`。仅特殊模板正文失效时，列表、普通模板正文、日期仍可能正确。当前既没有“特殊模板×content”的规则有效性状态，也没有规则版本及其导航、等待、提取节点的依赖关系；因此不能证明整工作流失效、按字段失效或只按路由失效哪一种是正确范围。字符串相同的选择器也不能直接当作同一依赖：它可能在不同页面作用域中代表不同元素。

## 3. 三个可实施研究问题

以下三个问题可以构成同一候选系统的连续控制步骤，但不能把尚未研发的组合写成现有能力。主方向应结合后续现有技术检索再选择。

### RQ1：在有限复验预算内，怎样区分访问故障与规则失效？

**具体问题。** 同一条旧规则在一次执行中失败；错误可能来自 HTTP 503、暂时超时、页面返回错误壳、正文迁移或字段确实不存在。立即使规则失效会破坏可用记忆，保持无限重试会重复消耗资源。

**输入。** 固定版本的旧规则、执行异常类别、最终 URL、状态码与时间、最近一次成功的字段局部证据、当前匹配/空值结果、允许访问的 URL 集及累计请求/输入预算。策略阻断作为终止事实，不参加绕过式复验。

**输出。** 可核验的 `访问暂不可用 / 规则疑似失效 / 字段本身缺失 / 无法区分` 及支持证据；另输出允许继续复用的范围、暂缓使用的范围或仍需取得的最小证据。网络事件不直接给选择器写入“已失效”。

**候选新控制机制。** 在若干可执行复验动作之间，依据它们能够区分的故障假设选择下一步，并把预算、证据来源、固定规则版本与状态转移绑定。动作可包括仅重复允许重试的 GET、对先前成功页面的相同字段作对照、取得失败 URL 的目标区域摘要。若观察结果仍同时支持多个原因，应停止并保留“未知”，而不是编造置信结论。仅增加重试、异常类型判断或故障计数是常规修复。

**最小实验。** 同一组旧规则和十个页面，分别注入一次 503 后恢复、持续超时、HTTP 200 错误壳、真实正文迁移和合法字段缺失。对比“任意失败即失效”“按错误类型固定重试”“固定对照复验”与候选自适应选择；给各组相同的两次额外请求预算。测错误停用仍有效规则的次数、真正规则失效未发现次数、判断不明比例、实际请求数和恢复后正确输出率。不能通过让候选组多请求来解释效果。

### RQ2：路由无法区分模板时，怎样发现小比例失效和非空错字段？

**具体问题。** 两个页面的 URL 都符合 `/news/{n}`，模板或字段区域却不同；旧正文选择器可能匹配空占位，也可能匹配一块足够长且各页不同的相关链接区域。加路由分层或提高唯一性要求均不能解决。

**输入。** 同一路由的候选 URL、旧规则和每字段来源区域的历史证据、当前提取值、局部 DOM 角色/邻接/祖先结构摘要，以及字段合同。局部证据应支持指出规则实际选中了什么区域；不以整页标签计数等同于字段身份。

**输出。** 需要进一步区分的同路由页面子集、字段区域是否与已验证证据一致、可定位到规则和页面子集的异常事实，以及必要的复验请求。证据不足以判断内容归属时输出待复核，不将非空默认当作正确。

**候选新控制机制。** 由字段提取冲突或局部证据不一致触发细分，选择能区分“模板变化”“正文缺失”“提取到其他区域”的页面和局部对照，并将取得的证据用于后续复用资格。通用 DOM 聚类、代表页抽样、长度阈值或额外 LLM 打分本身仍是常见方法；需要证明具体证据选择与记忆控制耦合后，在同等成本下有额外效果。

**最小实验。** 固定十个同路由详情，8:2 两种模板，依次设置：全部正确、两条空占位、两条非空侧栏、正文迁移但返回相同标签/类计数、正文正确但新增广告。对比当前路由门禁、全 DOM 指纹变化即重验、固定局部证据验证与候选机制。每个页面保存人工/fixture 确定的正文区域作为真值。测错误放行、无关变化误报、模板子集识别、补证请求和字节数。短正文、真正缺失正文应作为反例，不得只选有利于长度规则的样本。

### RQ3：怎样让已确认的局部失效只影响必要规则，并保留其他成功规则？

**具体问题。** 特殊模板正文规则已确认失效，但普通正文与 Wmeta 的标题、链接、日期仍有效。需要阻止 Wbody 的坏片段再次推荐，又不能因一次局部问题丢弃全部站点记忆。修复还可能引入对原成功页面的回归。

**输入。** RQ1/RQ2 产生的带原因和范围的证据、固定工作流/规则版本、字段提取与上游导航/等待的依赖、过去成功证据所覆盖的页面范围。输入中“未知”与“已确认失效”必须不同。

**输出。** 新的规则使用状态与允许范围、受影响依赖集合、保持有效的规则集合、修复候选版本、修复后必须复核的失败样本及成功对照。推荐阶段读取这些状态，避免重复选中已确认失效的片段。

**候选新控制机制。** 以证据限定失效传播，并在依赖发生共享、页面子集发生重叠时计算最小的必须暂停/复核范围；修复提交绑定旧版本、变更节点及失败/成功对照的复核结果。仅加 `invalid` 标志、TTL、失败次数、按字段扣分或缓存依赖图不应单独作为强创新。需要比较已有的逐字段健康分和整体工作流熔断，证明复杂控制优于常规状态维护。

**最小实验。** 预置 Wbody、Wmeta 两个工作流，普通/特殊两类详情。先全部验证成功，再仅改变特殊正文，并交替发送二十次正文/元数据请求；随后修复特殊正文，再注入“修复规则能处理特殊模板但误选普通模板”的回归。对比当前系统、整工作流停用、逐字段健康分/TTL、候选依赖与证据范围控制。记录重复使用已知坏规则次数、无关规则被停用数量、重新检查与抓取次数、修复后正确字段率及原成功页面回归率。所有候选使用相同初始记忆、模型和预算。

## 4. 研究与普通修复的验收分界

| 改动 | 合理定位 | 如何避免把常规实现包装成发明 |
|---|---|---|
| 补失败写回、更新失败计数和经验成功率、取消陈旧首选项 | 常规 bugfix / 实验底座 | 作为所有强基线的公共能力 |
| 增加状态码分类、有限重试、TTL、逐字段健康分 | 常规可靠性控制 | 必须列入对照，不能只比较当前未写回版本 |
| 增加 DOM 特征、模板聚类、代表页、长度检查、LLM 复核 | 可用技术组件 | 单个组件有效不能自动证明组合具有创造性 |
| 在预算内选择能区分故障原因的复验，并限制哪些证据可更新哪一份记忆 | 待检索、待研发的候选控制机制 | 相同预算下测误失效、漏失效和不明比例；明确停机/未知条件 |
| 把复验事实、字段局部身份、规则依赖及版本绑定，用于选择性失效和修复提交 | 待检索、待研发的候选控制机制 | 比较逐字段健康分、整体熔断及固定复验；测有效规则保留和回归 |

建议先实施纯确定性的受控实验，隔离记忆控制本身的效果；之后再接入固定模型测真实补证/修复成本。多智能体可以负责不同工作，但增加角色数量本身不是这里的技术解决方案，也不应额外引入难以归因的实验变量。

## 5. 本轮隔离诊断的复现

解释器：`D:/conda_envs/mytorch/python.exe`。在仓库根目录执行下列 Python 代码，不创建任务或数据库：

```python
from scripts.benchmark_verification_coverage import _spec, _records
from packages.validator import validate_records

same_route = _records(set())
for row in same_route[8:]:
    row["content"] = ""
report = validate_records(_spec(), same_route)
print(report.status, len(report.coverage_certificate.strata),
      report.coverage_certificate.failed_stratum_field_pairs)
# 实际输出：PASS 1 0；content 空值率为 0.2。

wrong_region = _records(set())
for index, row in enumerate(wrong_region):
    row["content"] = f"Distinct related-links text {index}"
report = validate_records(_spec(), wrong_region)
print(report.status, report.metrics.semantic_score,
      report.coverage_certificate.failed_stratum_field_pairs)
# 实际输出：PASS 1.0 0。
```

这些是当前检查能力的反例，不是新控制机制的有效性实验，也不是端到端网页抓取或模型修复实验。

完整可执行探针为 [probe_memory_patent_gaps.py](../../../scripts/probe_memory_patent_gaps.py)，本轮实际命令为：

```powershell
D:\conda_envs\mytorch\python.exe scripts/probe_memory_patent_gaps.py --output runtime/experiments/patent-memory-20261019/current_gaps.json
```

命令退出码为 0，表示诊断成功执行，而非质量检查应当通过。结果见 [current_gaps.json](../../../runtime/experiments/patent-memory-20261019/current_gaps.json)，逐案记录 fixture 的 `expected_quality_decision=FAIL` 与当前 `actual_validation.status=PASS`。同路由空正文案例的全局必需字段空值率为 0.0667，正文单字段空值率为 0.2；非空错区域案例的语义分数为 1.0。

脚本还用隔离临时 SQLite 手动调用 `record_failure`：调用前后首位工作流均为 ID 1，存储分数仍为 0.95；失败表有 1 行，绑定工作流的失败行数为 0。该探针仅证明“失败日志本身不改变当前排序或适用范围”，没有演示生产流程自动写回失败。临时数据库自动清理；没有导入会初始化真实仓库的 MCP server，也没有读取或修改用户数据库。JSON 保存关键源文件 SHA-256，便于后续实现后重跑比较。

## 6. 上轮证据的复用边界

上轮结果见 [results.json](../../../runtime/experiments/patent-coverage-20261007/results.json)、[实验协议](../EXPERIMENT_PROTOCOL.md)及 [test_coverage_gate.py](../../../tests/e2e/test_coverage_gate.py)。本轮只读计算了六个已有证据文件的 SHA-256，均与结果 JSON 一致：`coverage.py`、`report.py`、`models.py`、`server.py`、`benchmark_verification_coverage.py`、`test_coverage_gate.py`。因此可以继续引用与这些源文件版本绑定的历史结果；本轮没有重新运行或声称再次完成 500 项全套回归。

可复用：不同路由的少数模板空占位可被独立字段门禁发现；边界分散样本能在特定尾部案例提前停止；自动草稿失败不提交记忆；受控正确选择器可通过最终验证并提交；回执裁剪和 observed_records 范围声明已经实现。

不能外推：历史实验不是已存工作流反复复用后失效的实验；未证明失败原因辨别、动态记忆失效写回、同路由异模板识别、非空错区域识别、自动预算复验、局部依赖失效传播、修复后成功片段保留或真实模型 token 节省。历史两类路径是 `/news/` 和 `/special/`，不是同一 `/news/{n}` 下的隐模板。修复选择器由测试给定，不是智能体自主研发或修复效果。

上轮[专利对照](../PATENT_COMPARISON_2026-10-07.md)记录的现有技术已经覆盖模板/规则复用、网页变化监测、失败属性与代表页面反馈等广泛思路。旧检索可以作为本轮避免泛化主张的线索；不能替代本次针对“故障原因复验—限定证据更新范围—选择性失效—修复复核”的具体权利要求检索。本文三项均为候选研究问题，未给出“未检出即新颖”或“实验有效即有创造性”的结论。
