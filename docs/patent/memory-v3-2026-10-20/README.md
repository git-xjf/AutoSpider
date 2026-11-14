# 2026-10-20：继续专利研发的第三轮交付

> 2026-10-21 状态更新：用户已授权单次隔离公共 DNS 核验，核验与 robots 检查通过，已保存 3 份公开响应快照；临时模式已恢复，配置／信任名单未改。仍无完整浏览器资源包，不能宣称原站回放成功或形成新专利核心。见[本轮真实输入验证](../memory-live-2026-10-21/README.md)。下文是 10-20 的历史结果，“等待授权／strict 阻断”不再代表本轮采样状态。

本轮完成字段来源差分回放参考实现、冻结实验、近似公开和数据审计；**尚未形成一个已有充分差异依据、可直接申请的发明核心**。

在真实Chromium执行16个自编受控场景。B=6的34轮中，完整协议10次正确来源绑定、0次错误绑定、24次待证明；接受中仍有2次上游正文语义错误。新增25项回放测试及26项采集安全测试通过，最终全项目613项通过。差分方法已有直接先例，以上结果不能当作新算法优胜。

## 阅读入口

1. [RESULTS.md](RESULTS.md)：真实数字、代价、成功/失败案例和申请判断。
2. [PRIOR_ART.md](PRIOR_ART.md)：MUTAFLOW、Privacy Oracle、来源依赖、DOM Invader及中外专利要件对照。
3. [RESEARCH_DECISION.md](RESEARCH_DECISION.md)：唯一保留的问题、真实数据缺口和继续/停止门槛。
4. [DATA_AUDIT.md](DATA_AUDIT.md)：本地资料、静态外部数据与动态回放能力的差别。
5. [PROTOCOL.md](PROTOCOL.md) / [FIXTURES.md](FIXTURES.md)：任务声明来源、严格合同、冻结与公平性。

## 实现与证据

- [replay.py](../../../research/memory_v3/replay.py)：全离线浏览器回放、正负干预、预算与精确收据。
- [实验CLI](../../../scripts/benchmark_memory_source_binding_v3.py)、[冻结fixture生成器](../../../research/memory_v3/fixtures.py)。
- [独立测试](../../../tests/research/test_memory_source_binding_v3.py)。
- [正式结果](../../../runtime/experiments/patent-memory-v3-20261020/pilot.json)、[冻结manifest](../../../runtime/experiments/patent-memory-v3-20261020/fixtures-manifest.json)。

用户已选择项目已有公开新闻站，已核实财联社公开页面结构；[现场访问审计](LIVE_CAPTURE_AUDIT.md)明确区分通用HTTP可达与项目安全策略是否通过。目前项目strict检查因代理Fake-IP阻断，[受保护采集器最终预检回执](../../../runtime/experiments/patent-memory-v3-20261020/live-cls-20261020-final-preflight/report.json)为policy_blocked、0次robots/文档HTTP请求，不是站点回放成功。未更改策略或信任名单；继续采集需在授权后使用既有公共DNS核验，或取得脱敏HTML+HAR。

目的在于从真实失败约束研发，不能让正确来源Binding或语义标签免费进入在线算法。缺少完整输入时不将受控回放包装成真实站点专利效果。

既有[v2交底候选](../memory-v2-2026-10-19/TECHNICAL_DISCLOSURE_V2.md)及负结果保留，生产代码和用户数据库不接入此研究原型。
