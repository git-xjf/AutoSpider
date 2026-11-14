# V3 受控字段来源回放输入与评分

这组输入是自编 HTML、JavaScript 和 JSON 响应，运行在真实 Chromium 中。它不包含捕获的生产新闻站，不是第三方盲测，不用于声称新的差分算法优于已有工作。数据盘点及来源缺口见 [DATA_AUDIT.md](DATA_AUDIT.md)。

## 冻结与执行

在调用 replay engine 评估前已冻结 16 个场景、34 个任务轮次。冻结对象包括浏览器入口、每个响应的完整字节字符串/状态/方法/版本、ready selector、候选选择器、声明的 JSON entity/field binding，以及独立评分标签。评分标签不传入 engine。

| 对象 | SHA256 |
|---|---|
| manifest payload | `5606d99dec613ac4629c8e3911afc5c5ddd17dd811f97d924b0c565ee11a0cd9` |
| `research/memory_v3/fixtures.py` | `dc3b342b0da47f55035f99901fd708bb7ec52b75e27ce4b50307e8ae141b584e` |

```powershell
& 'D:\conda_envs\mytorch\python.exe' scripts/benchmark_memory_source_binding_v3.py --freeze-manifest runtime/experiments/patent-memory-v3-20261020/fixtures-manifest.json
& 'D:\conda_envs\mytorch\python.exe' scripts/benchmark_memory_source_binding_v3.py --manifest runtime/experiments/patent-memory-v3-20261020/fixtures-manifest.json --budgets 0 3 6 --store-mode memory --output runtime/experiments/patent-memory-v3-20261020/pilot.json
```

已有输出文件不覆盖。开始与结束核对 core/runner 源码哈希，不一致就拒绝保存。manifest 的 payload、fixture 源码及重新生成结果也须符合冻结哈希。

## 受控场景

| 场景 | 要观察的边界 |
|---|---|
| normal_no_schema | 无 schema.org 标记，正文由声明的 target.body 渲染 |
| recommendation_wrong_record | 推荐区或正文候选读取 foreign record |
| equal_text_different_entity | 两个 entity 初始文字相同，文字相等仍不能证明归属 |
| target_foreign_mixed_empty_control | foreign 初值为空，target-only 可伪通过；foreign 干预会污染候选 |
| duplicate_dom_body_role | 候选 selector 同时匹配两个正文节点 |
| duplicate_source_entity | JSON 中 target ID 重复，任务身份绑定不唯一 |
| missing_source_entity | JSON 中没有声明的 target ID |
| canary_sensitive_branch | 正常值读取 target，opaque canary 改变业务分支；保守待证明不等于没有实际依赖 |
| truncated_field_transform | 页面截断 target.body；任意变换依赖超出完整字段复制合同 |
| source_503 | 源响应不可用，不能当 selector 失效事实 |
| missing_replay_script | 缺少外部 renderer，必须报告未记录请求与不完整回放 |
| upstream_semantic_wrong_binding_right | 上游正文实际错误，但 DOM 忠实读取 target.body；绑定通过仍有语义错误 |
| unrelated_dom_side_effect | 旁路状态区域变化，不应否定正确局部候选 |
| same_entity_wrong_field | 同一个 entity 的 title 初值等于 body，候选却读取 title |
| static_equal_text_spoof | 常量文本碰巧等于 source body，没有字段依赖 |
| renderer_branch_snapshot_changes | 相同入口/API 下脚本及版本改变，旧证书必须 miss |

每个场景至少运行 initial 与 exact repeat；最后一场景另含 changed renderer 与 changed repeat。缓存按完整 capsule/binding/protocol 身份复用。只有已观测关系，不作跨快照、跨 entity 或跨语义内容的泛化。

## 比较和预算

完整协议使用两次 baseline、一项 target 字段干预，以及每个 foreign record 的字段干预。常规两条 JSON record 的最低完整加载次数为 4。所有候选共享每次 load 的实际 DOM 观察。B=0 保留冷缓存/预算不足边界；B=3 具有 baseline/baseline/target，但没有完成 foreign control；B=6 足以容纳这组普通完整协议。各次 load 使用全新 context，只允许冻结资源，缺少依赖的请求中止并计数。

三种决策从同一份浏览器 transcript 重算：

1. 文字相等只看 `baseline_1`，比较候选文字与声明 source 字段初值，选择第一个匹配候选。
2. target-only 消融只看完整的 `baseline_1 / baseline_2 / target` 前缀，使用 `base_match / baseline_stable / target_response`；不读取 foreign 路径稳定或 negative-control 结果。
3. 完整 paired 协议使用 engine 的唯一候选、稳定性及 foreign 非干扰判断，并可命中 exact memoization。

这是共享 transcript 的协议条件消融。报告所有实际 load、请求、fulfilled/aborted、浏览器启动和耗时，不能把它写成三个独立实现的运行时性能比较，也不能把离线文字比较称作零浏览器成本的同等试验。主矩阵可用内存 SQLite；另有独立磁盘 store 关闭重开检查，观察已成立证书在 B=0 时是否零 load 命中。

## 评分和论断边界

engine 只拿 `Bundle`、`Binding`、budget、store；评分阶段才读取 `expected_binding_selectors` 和 `semantic_reference`。分别统计错误来源接受、正确来源接受、待证明、漏掉的基线实际依赖，以及来源绑定正确但正文语义错误。

canary-sensitive 分支和截断变换故意保留为方法能力边界：它们存在实际 target 依赖，却不一定满足这个严格的完整字段复制干预合同。待证明不是已证明不存在因果关系。上游语义错误场景必须保留“绑定成立但正文错误”，不能写成所有正文都正确。

MUTAFLOW、DOM Invader 等已有动态差分/标记思想是需要正面对照的先例。本实验定位是检查一个具体工程机制是否可执行、可否定及代价多大，而非用对弱文字相等基线的改善直接宣称专利新颖性。

## 已运行的受控结果

冻结后正式执行的 `runtime/experiments/patent-memory-v3-20261020/pilot.json` 包含 102 个 task/budget 记录。每个预算对应 34 个任务轮次，包含重复任务；以下不是 34 个独立随机样本，也不能从这些定向压力用例推断真实站点准确率。

| 加载预算 | 决策方式 | 正确来源接受 | 错误来源接受 | 待证明 |
|---:|---|---:|---:|---:|
| 0 | 三种方式均相同 | 0 | 0 | 34 |
| 3 | 文字相等 | 10 | 8 | 16 |
| 3 | target-only | 10 | 2 | 22 |
| 3 | full paired | 0 | 0 | 34 |
| 6 | 文字相等 | 10 | 8 | 16 |
| 6 | target-only | 10 | 2 | 22 |
| 6 | full paired | 10 | 0 | 24 |

full paired 在 B=6 仍漏证 4 个基线实际 target 依赖轮次（canary-sensitive、截断各 initial/repeat），并接受了 2 个来源正确但正文语义错误的轮次（同一个上游错误场景及其 repeat）。它没有证明“正文零错”。

总费用含独立磁盘重开检查：180 次 page load、526 个请求、512 次 fulfilled、14 次 aborted、1 次 Chromium 启动，记录的 elapsed 合计约 49.381 秒。14 次 abort 出现在缺脚本受控输入。主矩阵有 5 次 exact cache 命中；独立磁盘检查另观察到 initial 4 loads、关闭重开后 B=0 命中且 0 load，trace hash 相同。更改 renderer 字节/版本后 cache miss 并重新消耗 4 loads。

正式结果绑定 core SHA256 `b80741b0d10e06f4c53647e669c24025e629f5be7cc348880e10c5ee117b0c36`、runner SHA256 `7a2c8f737eda91d09e3b17c1ff2b88eceb52d06ffd6f156ee1cdd97d91fabd96`，起止源码一致校验通过。实验本身没有对已知完整动态差分回放强基线建立新的优势；可用于说明可行性、必要的控制条件及不能解决的边界。
