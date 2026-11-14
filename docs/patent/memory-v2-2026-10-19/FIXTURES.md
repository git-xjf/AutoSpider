# V2 冻结输入与独立评分边界

本实验有 9 个开发场景、15 个工程 holdout 场景。每场景 10 页，包含重复任务；所有 URL 都使用同一路由形式，避免把路径差异当作真实模板身份。holdout 使用固定种子的独立生成分支，对页面顺序、URL、标题和模板组合进行变化。研究者可访问生成器和标签，因此这不是第三方盲测。

生成器为 `research/memory_v2/fixtures.py`，执行器为 `scripts/benchmark_memory_applicability_v2.py`。先写出完整 manifest，再运行评估：

```powershell
& 'D:\conda_envs\mytorch\python.exe' scripts/benchmark_memory_applicability_v2.py --freeze-manifest runtime/experiments/patent-memory-v2-20261019/fixtures-manifest.json
& 'D:\conda_envs\mytorch\python.exe' scripts/benchmark_memory_applicability_v2.py --manifest runtime/experiments/patent-memory-v2-20261019/fixtures-manifest.json --store-mode memory --output runtime/experiments/patent-memory-v2-20261019/pilot-aligned.json
```

已有文件不覆盖；重复运行时应另选结果文件名。评估开始、结束均检查 core 和 runner 源码 SHA256，一旦变化就拒绝保存；manifest 的 payload 和生成器源码也须匹配冻结哈希。

| 冻结对象 | SHA256 |
|---|---|
| manifest payload | `b2000fe14109e3d8cd0c3070ce3d9281be4a1d8da58f8b937d3b5f451ac1b2e7` |
| fixtures.py | `a343bd9b91782535115c67b5a1a58ec6808b6ea4dfa36139eda2bb303c5ecc60` |

## 场景范围

开发集保留 v1 的九类问题：无关 DOM 变化、全模板漂移、同路径尾部 8+2 变体、前缀变体导致错误泛化、临时失败后恢复、多个新模板族下预算不足、未见标题来源反例、已见反例、候选库没有正确修复。

| 工程 holdout | 检验边界 |
|---|---|
| shuffled_same_route_variants | 随机顺序的 8+2 变体，含 Article / NewsArticle |
| title_origin_mismatch | 非空正文来自另一标题，正确正文在候选库之外 |
| source_url_mismatch | 标题相同，但公开 Article URL 指向另一来源 |
| multiple_candidate_bodies | 同一候选匹配多个非空正文，无法取得唯一性证据 |
| body_role_missing | 正文角色标记缺失，应保持待证明 |
| source_markup_missing | 来源标记缺失，应保持待证明 |
| headline_markup_missing | 标题标记缺失，应保持待证明 |
| selector_version_identity | 选择器内容变化、重复任务、仅版本号变化 |
| contract_version_identity | 相同 HTML 下合同版本变化及重复任务 |
| 503_then_recovery | 临时 503 后恢复原网页，不应删除已成立的正文规则 |
| multifamily_budget_pressure | 多个变体、多个候选族，覆盖预算 0/1/2/4/10 |
| unrelated_edits_all_pages | 所有完整网页都变化，但合同相关事实不变 |
| unknown_repair_mixed_role | 未知修复与缺失角色组合，不允许偷读正确选择器 |
| contract_compliant_wrong_body | Article、标题、来源、角色全部合规，正文语义实际错误 |
| cold_start | 不温启动缓存；预算为零时不能凭空取得适用性证书 |

## 相同的信息和完整成本

所有策略获得同样的候选库 `#content`、`#article`、`.story-body`、公开快照、固定合同和完整关系投影。没有 generic `articleBody` 选择器给新策略额外兜底。`scoring_expected_text`、场景标签、变化描述均不传入策略。

正文适用性合同只覆盖可机械判断的来源关系：唯一非空候选、唯一 Article/NewsArticle 所有者、正文角色、属于该实体的唯一标题和来源 URL。独立嵌套 `itemscope` 的标题、来源和正文不能借用外层 Article 的身份。缺失证据保持待证明；明确不一致可否定当前适用性。该合同不具备任意网页正文语义判真能力。

所有策略都承担全部 HTML 解析、候选匹配和关系事实提取的共同成本。一个额外审核预算单位表示在已有快照投影上审核该页全部候选。完整投影已经计算标题、来源等关系，因此减少审核函数调用不等于减少整个验证计算，也不证明 HTTP、token 或 CPU 节约。

每个场景、预算、策略都有独立 SQLite store。温启动使用相同历史页面与 B=10，实际温启动开销与当前任务开销分列。冷启动场景跳过历史认证。正式 aligned 运行采用内存 SQLite，重复任务保留同一连接；这验证任务间缓存复用，不衡量磁盘重启持久化性能。磁盘版 `pilot.json` 是核心边界修复期间产生的中间诊断，不能作为最终版本证据或与内存版做速度比较。

## 合同合规与正文正确必须同时报告

评分在所有策略决策完成后读取独立的正文期望值，并从原网页重新检查公开合同；它不把缓存的 verdict 当作评分真值。

- `contract_valid_decisions`：通过固定公开合同的 VALID 决策数量。
- `contract_noncompliant_reuses`：策略宣称 VALID，但公开合同尚不成立的数量。
- `body_text_correct_decisions`：唯一正文文本与期望值相符的数量。
- `semantic_false_acceptances_despite_contract`：公开合同合规，但正文文本错误的数量。
- `correct_usable_rules_retained`：合同合规且正文文本正确的数量。
- `invalid_rule_reuses`：评分发现的错误或未经证明的 VALID 复用总数；包含合同外的语义错误，并非都可由当前合同证明为 INVALID。应结合以上分项解释。

`contract_compliant_wrong_body` 必须保留非零的正文错误；即使完整投影安全门没有合同违规，也不能写成“零错误正文采集”。候选与同信息的完整投影 greedy/FIFO 基线持平时，应保留平局，而不能归因于新的 memory 智能。
