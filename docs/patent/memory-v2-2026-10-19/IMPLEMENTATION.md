# 第二版隔离原型：实现与边界

代码：[core.py](../../../research/memory_v2/core.py)。测试：[test_memory_applicability_v2.py](../../../tests/research/test_memory_applicability_v2.py)。本原型没有接入生产记忆、采集器或工作流；SQLite由研究代码独立创建，不打开用户记忆库。

## 1. 修复的具体问题

旧版用结构相似性外推来源可靠性，在低预算下可能将一个已审核页面的成功推广到未审核的错误正文。第二版不再学习“尽量短”的外推条件，而是绑定固定合同所读取的全部关系结果。

输入为 `Snapshot`、版本化 `Rule`、版本化 `FieldContract`。`observe_page`实际解析快照，对每个候选收集：访问可用性、CSS有效性、匹配数、非空性、Article祖先数、正文角色、标题对应关系、来源URL对应关系。`audit_evidence`仅依赖这些明示事实。

文章标题、来源属性须属于对应Article的直接微数据作用域；嵌套Person等独立 `itemscope` 的属性不能补全外层文章缺失的证据，正文也不能跨独立微数据作用域借用外层来源。空标题不会因“空等于空”而得到有效认证。

## 2. 两层绑定

`scope_key`绑定投影器版本、源站、执行模式、规则指纹、合同指纹和全部合同事实。标题/来源保留对应关系而非原值，因此不同URL可以属于同一固定合同等价类。它不表示这些页面属于相同语义主题，更不表示正文内容真实。

`binding_hash`另外绑定具体URL、快照哈希、规则身份、选择器、模式及投影，用于检查证据对象在生成后是否被不一致地改写。它不是数字签名、安全认证或可信来源证明：预计算观测和写入Verdict属于受信任的研究API；恶意调用方若同时伪造内容和所有哈希，不能由这个检查识别。

合同或规则的声明版本、选择器文本、字段名、投影器/验证器实现版本、源站或执行模式改变，都会阻止沿用旧key。实现改变后需要主动更新实现版本；原型未实现源码变化自动版本化。

## 3. 收据与状态

| 状态/通道 | 条件与行为 |
|---|---|
| VALID | 确定性来源合同通过；同key无冲突时可以复用该合同判定 |
| INVALID | 无匹配、空值、明确标题/来源不匹配等已被固定合同反驳的范围；不是全站永久禁用该选择器 |
| PENDING | 未审核或缺少唯一来源、角色、标题等证明；不输出有效规则 |
| quarantine | 同key被写入矛盾的VALID/INVALID收据，持久隔离，不以多数票恢复 |
| transport | 503/429等临时失败、401/403策略阻断或未就绪分别记录，不写为选择器无效 |

`MemoryStore`包含 `receipts`、`quarantines`、`transport` 三表。具体快照收据只追加去重；无收据的新范围在决策输出中表现为PENDING，不必先生成一条持久状态行。证据恢复后按当前范围重查；暂时不可访问不会删除此前有效收据。

冲突隔离由人工矛盾写入测试验证。对于无缺失依赖的确定性合同，同key本来就应得出同判定；本轮没有自然出现的同key矛盾，也不把注入冲突当成识别能力提升。

## 4. 审核调度

`evaluate_policy`在预算内选择待审核页面，一页审核全部共享候选。greedy排序按该页可以覆盖的、尚无收据的 `(页面,规则)` 范围数量最大优先，稳定序号破同分。它是覆盖排序，不是因果归因、最优主动学习或学习到的差异严重度。

通过审核的结果只传播到同一完整证据key。不在候选库中的真实修复规则不会被自动生成。规则库中如果存在另一个已证明有效的选择器，可以选择它；这叫候选选择，不叫自主研发新规则。

候选 `counterexample_scoped` 与 `complete_projection_greedy` 故意共享完整信息、安全门和排序实现。该对照用来检查“候选是否只是完整依赖缓存”的等价性，不能冒充两个独立算法的胜负实验。`complete_projection_fifo`分离顺序差异，`exact_url_receipts`分离跨URL等价共享的效果。

## 5. 不能据此推出的结论

固定合同并非语义真值oracle：将错误正文放在自洽Article标记下仍会通过。没有来源元数据的普通网页可能大量待审核，不能假设其覆盖率与受控样本相同。并行多智能体写入、数据库迁移、过期收据清理、在线访问、动态网页抽取、模型修复、合同学习及真实站点回放均未在此实现。

投影已计算合同的核心关系。减少后续轻量if判断或收据写入次数，不等于节省下载、模型或总CPU成本。哈希、SQLite与覆盖排序自身还会产生开销。实际结果与成本见 [RESULTS.md](RESULTS.md)；专利风险见 [PRIOR_ART_AND_CLAIMS.md](PRIOR_ART_AND_CLAIMS.md)。

## 6. 复现

```powershell
D:\conda_envs\mytorch\python.exe -m pytest tests\research\test_memory_applicability_v2.py -q -o addopts=''
D:\conda_envs\mytorch\python.exe scripts\benchmark_memory_applicability_v2.py --manifest runtime\experiments\patent-memory-v2-20261019\fixtures-manifest.json --output runtime\experiments\patent-memory-v2-20261019\rerun-new-name.json --store-mode memory
```

结果文件按新文件名追加，不能用同名命令覆盖已有证据。原始冻结manifest不重新生成、不按结果修改。
