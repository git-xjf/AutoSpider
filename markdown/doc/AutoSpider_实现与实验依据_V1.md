# 实现与实验依据 V1

核验日期：2026-10-29。对应[专利初稿](AutoSpider_发明专利初稿_V1.md)，是审阅附件，不属于拟提交说明书正文。

## 1. 本轮实际做了什么

只读取现有实现，运行既有受控测试与机制复现脚本，撰写初稿、对照分析和附图；**没有研发或接入新门控，没有修改生产代码、配置、原有专利材料或用户记忆库**。测试通过不等于法律上的新颖性或创造性成立。

模型修复、图谱记忆、自动补证访问、硬预算消费、跨版本证明和全部模板覆盖均未被本稿冒称为已完成。上一轮不同研究线的负结果不删除，但不以其直接否定本稿控制链。

## 2. 权利要求与源码的对应

以下为2026-10-29读取版本的位置，行号是定位参考；代码更新后应以同目录源码指纹复核。

| 权项／要素 | 源码入口 | 支持的实际行为 |
| --- | --- | --- |
| 权1／F1：字段、计划、阶段、记录数 | [草稿执行](../../services/easyspider_mcp/server.py)、`crawl_draft_execute`／`crawl_execute_validated` | 服务生成并执行样本／最终阶段，返回实际记录数并使用相应字段合同。 |
| 权1／F2、权2：URL分层 | [coverage.py](../../packages/validator/coverage.py)，`route_signature`，约42行 | 主机、路径占位、查询键组成分组标识；不使用查询值。 |
| 权1／F3、权3：层×字段判据 | 同文件`analyze_stratum_coverage`，约136行；[report.py](../../packages/validator/report.py) | q超过阈值产生高严重度问题，加入总体硬失败判断。 |
| 权3：空值定义 | [metrics.py](../../packages/validator/metrics.py)，`is_null` | None、空白字符串视为空；通过row.get使字段缺失同样计为空。 |
| 权1／F4、权4—5：补证目标 | coverage.py的`_failed_representative_urls`，约108行；`EvidenceRequest` | 映射实际失败字段与URL，贪心选最多两个地址；保留原查询值；输出范围参数但不执行访问。 |
| 权1／F5、权6：观察范围 | [models.py](../../packages/crawl_core/models.py)，`CoverageCertificate`／`ValidationMetrics` | `scope=observed_records`、字段对总数／失败数与`complete`；另外比较校验数和最终输出数。 |
| 权1／F6、权6—7：提交条件 | server.py的`_finish_draft_result`，约755行 | 综合PASS、最终阶段、`validation_complete`及非策略阻断时，才走本次自动提交。 |
| 权7：阶段处理 | server.py的`crawl_draft_execute`，约795行 | 小任务直接最终执行；其他任务样本通过再最终执行，样本失败不入库。 |
| 权8：回执控制 | server.py的`_validation_receipt`，约84行 | 裁剪展示详情，保留判定及聚合指标；保留的补证URL不截断。 |
| 权9：功能模块 | 上述服务与校验函数的功能映射 | 模块为功能划分，不表示新增了与图中名称相同的Python类或专用硬件。 |
| 权10—12：计算机执行实现 | [编译器](../../packages/compiler/playwright.py)、[任务草稿](../../packages/crawl_core/drafts.py)及上述程序 | 通用计算机执行软件方法，未宣称已经制作新硬件设备。 |
| 记忆写入对象 | [repository.py](../../packages/site_memory/repository.py)，`commit_validated` | 保存页面模型、选择器、工作流、任务摘要／关联标识和评分；不自动保存为不可伪造执行证明。 |

### 已核对的两个不同“完整”标记

- `coverage_certificate.complete`：输入记录集合非空、观察分层的必填字段没有超阈值失败。它不包括所有质量检查，也不是全站覆盖。
- `validation_complete`：当前已校验记录数覆盖本次实际输出总记录数。它不证明任务漏项、真实语义或未访问页面已被检查。

最终提交同时依赖综合PASS、执行阶段和后者；提交接口还拒绝与失败分层证据矛盾的PASS。兼容手工接口仍可接受未带证书的旧PASS，因此初稿只主张自动工作流路径，未主张所有外部入口统一强制。

## 3. 本轮针对性回归

实际运行：

```powershell
D:\conda_envs\mytorch\python.exe -m pytest tests/unit/test_coverage_validation.py tests/unit/test_validation_receipt.py tests/unit/test_mcp_draft_execution.py tests/e2e/test_coverage_gate.py -q -s -o addopts='' -p no:cacheprovider --basetemp=.research/pytest-patent-draft-20261029-r1 --junitxml=markdown/doc/核验资料/targeted-tests-20261029.xml
```

实测 **20 passed、1 warning，8.78秒**。警告为既有Pydantic Settings的`lifespan`前向引用提示。完整计数见[JUnit XML](核验资料/targeted-tests-20261029.xml)，完成输出见[pytest回执](核验资料/pytest-completion-20261029.txt)。这不是20个网站或20次独立真实事故。

其中，两种采样策略通过本地HTTP服务器、真实编译器、原生HTTP执行器、草稿服务和独立SQLite库执行；其他测试包含替身适配器和函数单测。测试由[conftest.py](../../tests/conftest.py)隔离模块级运行／记忆目录。没有收费模型调用，没有公网新闻采集；文献浏览的网络访问不计入采集实验。

### 两种本地HTTP流程的实测结果

| 场景 | 已校验条数 | 重建原总体判据 | 当前分层判据 | 自动提交 | 列表GET／详情GET |
| --- | ---: | --- | --- | --- | --- |
| 错误`.content`，前缀样本后最终阶段 | 10 | PASS | FAIL，1个失败字段对 | 否 | 2／15 |
| 错误`.content`，边界分散样本阶段 | 5 | PASS | FAIL，1个失败字段对 | 否 | 1／5 |
| 修正`.content, .alternate`，前缀策略 | 最终10 | 不作为改进判据 | 样本PASS／最终PASS | 是 | 2／15 |
| 修正`.content, .alternate`，分散策略 | 最终10 | 不作为改进判据 | 样本PASS／最终PASS | 是 | 2／15 |

四行来自两个参数化案例，每例先错误再修正，合计57次本地测试页面GET，不含其他测试替身、图形预览或文献访问。原总体判据由测试函数在实际抽取结果上重建，**不是重新运行一套归档旧系统**。修正规则由测试提供，不是模型自主修复实验。

主故障例共有10条记录、3个必填字段，空正文2个：全局空单元格率2/30，正文全局空值率2/10，special层正文空值率2/2。当前门控识别的是最后一项超过阈值，而不是把任何空值都视作失败。

### 其他测试支持

所选测试还覆盖实际失败URL贪心选择、查询值保留、两个字段分别失败时选两个页面、观察范围声明、回执总字节限制、长URL不被截断、失败证书与PASS矛盾时拒绝、样本失败早停、小任务直接最终执行，以及1000条前缀PASS不能提交1500条最终工作流。

1500／1000案例使用服务测试替身验证条件，未实际访问1500个网页。回执裁剪案例不证明token账单下降或模型决策正确。

## 4. 确定性机制复现

再次运行既有`scripts/benchmark_verification_coverage.py`，保存原字段输出为[机制复现JSON](核验资料/coverage-benchmark-20261029.json)。这是无网络的合成记录诊断，不能和本地HTTP场景混成真实网站样本。

| 合成位置 | 前缀样本＋分层门控 | 分散样本＋分层门控 | 最终全部记录＋分层门控 |
| --- | --- | --- | --- |
| 末尾两条失败 | PASS | FAIL | FAIL |
| 中间两条失败 | FAIL | FAIL | FAIL |
| 单个未采中异常（索引8） | PASS | PASS | FAIL |

第三行是必要反例：分散取样仍会漏掉失败，不能把它写为“覆盖所有模板”。不将若干人为位置窗口上的命中数当线上统计置信度或新颖性证据。

## 5. 效果主张的允许范围

本稿可以说明：在分组能够隔离观察到的失败路由时，减少总体均值掩盖集中空值；失败URL不是任意正常页；质量与验证范围共同控制自动记忆提交；有界回执不会把已计算FAIL改成PASS。

不应写成：准确率普遍提升某百分比、全站全模板覆盖、不会错抓非空内容、自动修复率提高、图谱带来收益、普遍节省固定token／成本／时延、相对全部现有技术最优。本轮没有为这些效果取得证据。

## 6. 未完成项与初稿收窄

| 未完成或不保证 | 初稿处理 |
| --- | --- |
| 自动执行补证抓取及累计硬预算 | 只写“生成含范围参数的补证请求”。 |
| 修复版本与全部证据内容的不可伪造绑定 | 不以已有`task_hash`冒充已完成统一认证链。 |
| 只重跑受影响分层并对所有成功层回归 | 不写为完成实施步骤。 |
| 路由等于DOM模板或自动发现全部模板 | 只写路由近似分层及已观察范围。 |
| 自动流式校验任意大结果文件 | 保留当前读取上限与范围不足时不提交。 |
| 所有手工入口与自动门禁完全一致 | 权利要求限定自动工作流内的提交路径。 |
| 多智能体提示词部署完全一致 | 不以角色提示词作为确定性门控依据；未作收费模型验收。 |

## 7. 指纹、不变性与附图检查

相关源码、测试与用户记忆库的读后指纹在[source-fingerprints-20261029.json](核验资料/source-fingerprints-20261029.json)。本轮开始与回归后的coverage、report、models、server、E2E测试源码哈希一致；生产记忆库始终为 `b6bb171ea460c4a86e73a651269e42a382279185fa2ab6c6b44e7508fa26e504`。未覆盖旧冻结实验。

附图为本轮直接编写的黑白SVG。已用只加载本地内容、阻断外部HTTP的Chromium渲染检查：[图1预览](核验资料/图1_方法流程_审阅预览.png)、[图2预览](核验资料/图2_系统结构_审阅预览.png)。首次直接打开SVG时截图超时，改为本地HTML容器后成功；不把首次失败写成完成。附图标记与说明书一致，预览仅供屏幕审阅，不代表正式缩放／页边距已符合提交要求。
