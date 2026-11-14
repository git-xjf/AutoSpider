# AutoSpider 多智能体爬虫系统技术汇报

> 核验日期：2026-10-27。用途：内部技术汇报、导师讨论和后续研发梳理。
>
> 本文以当前本地源码、deer-flow/config.yaml、deer-flow/extensions_config.json 和已安装的 crawler-lead 配置为依据。它是一份静态代码与磁盘配置审阅报告，不代表对某个正在运行的服务进程做过完整现场验收。本次没有调用收费模型、抓取新网站、修改智能体配置或重启服务。
>
> 文中的“代码强制”“配置限制”“提示词约定”分别标注，不能把模型被要求遵守的规则当作已经由程序绝对保证。源码节选省略了部分日志、异常处理或元数据；讲解示例不是实际爬取日志。

## 阅读导航

1. [汇报结论与总体架构](#overview)
2. [智能体角色与职责](#roles)
3. [提示词的来源与组装](#prompts)
4. [完整协作工作流](#workflow)
5. [智能体通信及消息传递代码](#communication)
6. [共享记忆与上下文处理](#memory)
7. [Tools、MCP 与 Skills 的区别和权限](#capabilities)
8. [全部 AutoSpider MCP 工具清单](#mcp-inventory)
9. [六个 Skills 与实际有效范围](#skills)
10. [执行引擎、QA、模型与资源控制](#execution)
11. [配置、可观测性、当前差异与边界](#boundaries)
12. [源码定位索引及常见答辩问题](#sources)

<a id="overview"></a>

## 1. 汇报结论与总体架构

### 1.1 一句话介绍

AutoSpider 是一个以 DeerFlow 编排智能体、以 MCP 暴露采集能力、以固定 Python 执行器运行 HTTP/JSON/Playwright 采集计划的网页数据采集系统。

模型负责理解需求、分析网页证据和提出规则；程序负责参数校验、执行、确定性质量检查、结果存储和验证后的工作流复用。

### 1.2 汇报时应先讲清的七个事实

1. 项目配置了三个爬虫角色，但不要求每次任务都启动三个角色。
2. crawler-lead 是唯一的爬取执行编排者；另外两个角色提供只读分析或修复建议。
3. 三个角色都有独立的角色提示词，但最终模型输入还包含框架提示、任务消息和必要上下文。
4. 当前通信是主从式任务委派，不是三个智能体互相广播或群聊。
5. 智能体之间使用 task 的任务消息与结果消息通信；MCP 是调用采集工具的通道，两者不是同一件事。
6. 不共享完整聊天历史；站点记忆、用户长期记忆、文件、临时浏览器会话分别有不同的共享范围。
7. 当前原生采集不需要 EasySpider 桌面软件和上游执行程序，但仍使用 DeerFlow、Playwright、httpx 等组件。

### 1.3 分层架构

~~~mermaid
flowchart TD
    U["用户：URL、字段、页数、条数"] --> L["crawler-lead：需求与执行编排"]
    L -->|"task：复杂结构分析"| W["web-analyst"]
    W -->|"分析结果 / 选择器 / 不确定项"| L
    L -->|"task：QA FAIL 后诊断"| Q["qa-repair"]
    Q -->|"质量报告 / 最小修复建议"| L
    L -->|"获准的 MCP 工具"| M["AutoSpider MCP 服务"]
    W -->|"只读探针工具"| M
    Q -->|"只读结果、日志与验证工具"| M
    M --> T["CrawlSpec / PageModel / CrawlPlan"]
    T --> E["原生 HybridExecutor"]
    E --> H["http_html / http_api"]
    E --> B["playwright"]
    H --> V["程序 QA 与覆盖检查"]
    B --> V
    V --> F["结果文件与运行记录"]
    V -->|"完整验证通过"| S["SQLite 站点工作流记忆"]
    S --> M
~~~

这里的“多智能体”属于中心协调、职责分离、按需委派；不属于去中心化协商网络。

### 1.4 项目角色和框架角色不是一回事

| 名称 | 实际含义 |
|---|---|
| crawler-lead | 本项目的自定义爬虫主智能体配置 |
| lead_agent | DeerFlow 底层主智能体工厂/运行图；crawler-lead 复用这套框架 |
| web-analyst、qa-repair | 注册在 subagents.custom_agents 下的两个爬虫子角色 |
| general-purpose、bash | DeerFlow 可提供的通用子角色，不在 crawler-lead 的委派白名单内 |
| AutoSpider MCP 服务 | Python 工具服务，不是第四个 LLM 智能体 |
| HybridExecutor、QA validator | 确定性程序模块，不是独立模型角色 |

<a id="roles"></a>

## 2. 三个智能体分别负责什么

| 角色 | 触发条件 | 主要输入 | 主要职责 | 输出 | 明确边界 |
|---|---|---|---|---|---|
| crawler-lead | 用户提交采集需求 | URL、字段、页数/条数、记忆命中、探针证据 | 理解需求，决定复用或分析，组织工具调用，启动执行，处理失败并交付 | 实际结果数量、QA、完整性、任务 ID、输出路径 | 不能编造成功；不能把策略拦截当选择器故障绕过 |
| web-analyst | 主智能体判断复杂结构、客户端渲染或现有证据不足 | 有界网页证据、目标字段、URL、已测选择器 | 只读分析列表/详情/分页，测试选择器，判断传输模式 | 紧凑草稿参数、匹配计数、证据缺口与不确定项 | 工具白名单不包含任务注册和执行；不能擅自开始采集 |
| qa-repair | 程序 QA 返回 FAIL，主智能体需要进一步诊断 | 执行 ID、质量报告、失败字段、有限日志/样例 | 识别质量问题，区分规则缺陷与传输不适用，提出最小修复或重新分析建议 | ValidationReport / RepairPatch 建议或 REQUEST_REANALYSIS | 不能启动全量任务，不能直接修改编译后的执行清单 |

角色边界同时有两层：

- 提示词要求：例如“只有 Lead 执行”“QA FAIL 后才委派修复”。是否决定委派，仍由主模型根据这些要求判断。
- 工具装配限制：子角色只拿到配置白名单内的业务工具；原生子角色不启用 task，不能再递归委派。

“QA/Repair 返回符合 schema 的报告”是角色输出要求，不意味着所有子角色最终文本天然都是经过 Pydantic 校验的消息。真正提交到 MCP 的类型化参数还要在服务入口校验。

### 2.1 当前配置摘录

来源：[crawler-lead/config.yaml](../deerflow/agents/crawler-lead/config.yaml)。

~~~yaml
prompt_profile: crawler_compact
tool_names:
  - task
  - ask_clarification
  - present_files
  - read_file
  - describe_skill
allowed_subagents:
  - web-analyst
  - qa-repair
force_subagent_enabled: true
thinking_enabled: false
lock_thinking_enabled: true
token_budget:
  enabled: true
  max_tokens: 200000
  warn_threshold: 0.7
  hard_stop_threshold: 1.0
~~~

force_subagent_enabled 表示启用委派能力，不表示每次必须调用子智能体。

当前 deer-flow/config.yaml 的普通 task 总委派次数上限为每个 Lead run 2 次；这限制的是次数，不是保证“一次分析加一次修复”的固定分配。同一会话的下一次用户运行有新的 run 边界。

<a id="prompts"></a>

## 3. 每个智能体有单独提示词吗

有，但要区分“角色提示词文件”和“最终发送给模型的全部消息”。

### 3.1 提示词文件与生效位置

| 角色 | 仓库维护位置 | 磁盘运行配置来源 | 组装方式 |
|---|---|---|---|
| crawler-lead | deerflow/agents/crawler-lead/SOUL.md | deer-flow/.deer-flow/users/{user_id}/agents/crawler-lead/SOUL.md | 框架紧凑提示词 + 用户范围的 SOUL + skills 索引 + 延迟工具目录 |
| web-analyst | deerflow/agents/web-analyst/SYSTEM.md | deer-flow/config.yaml → subagents.custom_agents.web-analyst.system_prompt | 子角色提示词 + 框架报告约定 + 技能/工具发现元数据 |
| qa-repair | deerflow/agents/qa-repair/SYSTEM.md | deer-flow/config.yaml → subagents.custom_agents.qa-repair.system_prompt | 同上，但使用独立的 QA/Repair 角色文本 |

重要：修改仓库里的 SYSTEM.md 不会自动改写已经复制进 config.yaml 的文本。脚本 scripts/bootstrap.py 才负责安装/同步这些配置。用户范围的技能和 SOUL 也有安装副本。

### 3.2 三个角色提示词的关键内容

以下是当前角色文本的短摘录，不是完整最终 system prompt：

~~~text
Crawler Lead:
Only the lead executes. Complex pages may use one bounded read-only
web-analyst; static sites need no worker. qa-repair only after FAIL.

Web Analyst:
Reuse the lead's compact evidence; do not repeat sufficient probes.
Never create, register or execute tasks.

QA/Repair:
Determine whether records satisfy CrawlSpec; process success is not data success.
You may inspect results, logs, snapshots, and selectors, but you must never
register or start a full task and must never edit the compiled task.
~~~

主角色还要求保留用户原始字段、页数和条数限制；参数错误只做有界修正；不得把 DOM、全文或完整工具 schema 反复放进模型上下文。

### 3.3 主智能体的提示词组装

源码：[lead_agent/agent.py](../deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py)，约 L1201；[lead_agent/prompt.py](../deer-flow/backend/packages/harness/deerflow/agents/lead_agent/prompt.py)，L1049 的 apply_crawler_prompt_template。

~~~python
# 源码调用链节选；省略其他参数和普通 profile 分支。
if profile == "crawler_compact":
    from deerflow.agents.lead_agent.prompt import apply_crawler_prompt_template

    system_prompt = apply_crawler_prompt_template(
        agent_name=agent_name,
        user_id=resolved_user_id,
        skill_names=skill_setup.skill_names or frozenset(skill.name for skill in enabled_skills),
        deferred_names=setup.deferred_names,
        subagent_enabled=subagent_enabled,
        allowed_subagents=allowed_subagents,
        max_concurrent_subagents=max_concurrent_subagents,
        max_total_subagents=max_total_subagents,
        app_config=resolved_app_config,
        subagent_execution_capacity=subagent_execution_capacity,
    )
~~~

紧凑模板的内容包括信任边界、需求保持、三模式选择、重复执行约束、真实结果交付要求；随后拼接 get_agent_soul(...)、skills 索引与延迟工具目录。不是只把一份 SOUL.md 发给模型。

### 3.4 子智能体的提示词组装

源码：[subagents/executor.py](../deer-flow/backend/packages/harness/deerflow/subagents/executor.py)，约 L1200—L1263。

~~~python
# 真实组装逻辑的连续关键步骤；中间的 skills/工具目录拼接已省略。
system_parts: list[str] = []
if self.config.system_prompt:
    system_parts.append(self.config.system_prompt)

verification_cfg = getattr(resolved_app_config, "verification", None)
receipts_enabled = getattr(verification_cfg, "receipts_enabled", True)
system_parts.append(build_report_contract_section(receipts_enabled=receipts_enabled))

# ...这里还可能追加技能索引、延迟工具目录及验收标准的位置说明...
messages: list[Any] = []
if system_parts:
    self._assembled_system_prompt = "\n\n".join(system_parts)
    messages.append(SystemMessage(content=self._assembled_system_prompt))

task_content = f"{task}\n\n{criteria_block}" if criteria_block else task
messages.append(HumanMessage(content=task_content))
~~~

模型生成的委派任务和 acceptance_criteria 属于任务数据，进入 HumanMessage；不会因为由主智能体产生，就自动获得系统指令优先级。网页、工具输出、记忆和委派报告也按不可信数据处理。

<a id="workflow"></a>

## 4. 三个角色如何协同完成一次爬取

### 4.1 有兼容站点记忆

1. Lead 解析目标 URL、字段与数量要求。
2. 调用 memory_lookup_site，默认只取元数据，不取完整 PageModel。
3. 找到已验证且适用的 workflow 后，调用 crawl_reuse_workflow。
4. 服务克隆并适配计划，运行必要样本与 QA，再执行全量；小规模明确条数目标可以只执行一次。
5. 返回质量摘要、数量和文件路径。

这条路径通常不需要 Web Analyst，也不需要 QA/Repair 模型再次解释 PASS。

### 4.2 没有站点记忆的新网站

1. Lead 先用 probe_http 查看紧凑 HTML/JSON 证据。
2. 用 probe_http_select 针对完整响应测试列表、字段和详情选择器。
3. 原始 HTML 足够时，Lead 可以直接完成分析。
4. 遇到复杂结构或浏览器独有证据时，才委派一次有界 web-analyst。
5. Lead 汇总分析结果，通过 crawl_draft_create 创建草稿。
6. 调用 crawl_draft_execute，由服务完成正常的编译、样本、QA、全量及验证后记忆提交。

### 4.3 QA 失败后的修复

1. 程序返回 FAIL、失败字段及有界诊断信息。
2. Lead 根据必要性委派 qa-repair；工具参数错误不等同于 QA FAIL。
3. QA/Repair 读取获准的摘要、日志与选择器证据，返回最小修改建议。
4. Lead 使用修正后的证据创建新草稿或走高级修复入口，再验证。
5. 策略/域名/私网阻断不进入选择器修复，不通过切换模式规避。

提示词要求默认最多三轮修复后 NEEDS_HUMAN，但当前 task 的每 run 总委派上限为 2。不能将“最多三轮修复”解释为程序一定允许再启动三个 QA 子智能体；还要同时满足委派、轮数、时间和 Token 限制。

### 4.4 协作过程图

~~~mermaid
sequenceDiagram
    participant U as 用户
    participant L as crawler-lead
    participant W as web-analyst
    participant M as AutoSpider MCP
    participant Q as qa-repair
    U->>L: URL + 字段 + 页数/条数
    L->>M: memory_lookup_site
    M-->>L: 可复用工作流或未命中
    alt 可复用
        L->>M: crawl_reuse_workflow
    else 新站
        L->>M: probe_http / probe_http_select
        M-->>L: 有界结构证据
        opt 复杂页面需要专家
            L->>W: task(prompt, subagent_type)
            W->>M: 获准的只读探针
            M-->>W: 证据
            W-->>L: task 工具结果
        end
        L->>M: crawl_draft_create
        M-->>L: draft_id
        L->>M: crawl_draft_execute
    end
    M-->>L: 执行结果、程序 QA、输出路径
    opt QA FAIL 且仍有委派预算
        L->>Q: task(失败证据与执行 ID)
        Q->>M: 结果摘要 / QA / 日志
        Q-->>L: 最小修复建议
        L->>M: 新草稿或高级修复后再验证
        M-->>L: 实际验证结果
    end
    L-->>U: 完整性、QA、数量和文件
~~~

这是业务协作图，不代表每个框都是固定 LangGraph 节点，也不代表 Lead→Web Analyst→QA/Repair 必定顺序全部执行。

<a id="communication"></a>

## 5. 智能体之间如何通信：三种通道要分开

| 通道 | 参与方 | 当前方式 | 传递内容 |
|---|---|---|---|
| 智能体委派 | Lead → 子角色 → Lead | DeerFlow 原生 task、SubagentExecutor、消息状态更新 | 任务字符串、最终报告、执行状态与元数据 |
| 工具调用 | 获准的智能体 → AutoSpider | MCP，当前使用 stdio | 结构化工具参数、工具结果、诊断与文件/任务句柄 |
| UI 进度 | DeerFlow 后端 → 浏览器界面 | task_* 自定义事件，经流式运行接口传递 | 子任务开始、进度、完成/失败、模型与用量信息 |

没有发现当前爬虫角色通过 A2A、RabbitMQ、Kafka 或自建 HTTP 接口彼此直接对话。不能把底层模型 API 的 HTTP 请求当成智能体之间的通信接口。

### 5.1 第一步：主模型产生 task 工具调用

以下是协议形状的讲解示例，不是一次真实运行的日志：

~~~json
{
  "name": "task",
  "args": {
    "subagent_type": "web-analyst",
    "description": "分析列表和详情字段",
    "prompt": "目标 https://example.org/news；字段 title/url/publish_time/content；2 页。已有 HTTP 证据显示列表存在，详情正文结构未确定。只验证缺失的详情与分页证据，返回 mode、选择器、计数和不确定项；不要注册或执行任务。"
  },
  "id": "call_example_001"
}
~~~

task 的业务参数是 prompt、subagent_type，以及可选 description、acceptance_criteria。runtime 和 tool_call_id 由框架注入。description 只用于进度标签，不决定子角色实际执行内容。

prompt 是字符串。可以在里面组织 JSON，但当前不是一套强制的、独立的“智能体间 JSON 消息协议”。

### 5.2 第二步：后端派发子智能体

源码：[task_tool.py](../deer-flow/backend/packages/harness/deerflow/tools/builtins/task_tool.py)，L646、L859—L924。以下为真实调用链缩略展示，变量由周围源码建立：

~~~python
# 为子角色取工具时关闭再委派能力。
available_tools_kwargs = {
    "model_name": effective_model,
    "groups": parent_tool_groups,
    "subagent_enabled": False,
    "include_upload_tool": upload_state_available,
}
tools = get_available_tools(**available_tools_kwargs)

# 实际 executor_kwargs 还包含 config、tools、身份、线程和验收标准等。
executor = SubagentExecutor(**executor_kwargs)
execution_id = executor.execute_async(prompt, task_id=tool_call_id)
~~~

关键点：

- subagent_type 先经过允许角色检查和角色解析，不是任意名称都能启动。
- 当前 crawler-lead 只允许 web-analyst、qa-repair。
- 子角色自身不启用原生 task，避免无限递归。
- 内部提交到持续存在的子智能体事件循环；不是给每个角色单独部署一个聊天服务器。

### 5.3 第三步：建立子智能体独立上下文

子角色初始消息是“自身 system + 当前委派 HumanMessage”，不是父聊天历史的全量复制。

任务工具另行传递 sandbox、thread_data、经过校验的 uploaded_files，以及 thread_id、user_id、run_id 等身份/关联信息。共享运行环境与共享完整消息历史是不同概念。

源码中的状态建立节选：

~~~python
state: dict[str, Any] = {"messages": messages}
if self.sandbox_state is not None:
    state["sandbox"] = self.sandbox_state
if self.thread_data is not None:
    state["thread_data"] = self.thread_data
if self.uploaded_files is not None:
    state["uploaded_files"] = deepcopy(self.uploaded_files)
~~~

身份关联通过 runtime context 传递，源码节选：

~~~python
context: dict[str, Any] = {}
if self.thread_id:
    context["thread_id"] = self.thread_id
context["user_id"] = self.user_id
context["user_role"] = self.user_role
context["run_id"] = self.run_id
~~~

这里相同的业务 thread_id 用于工具、存储、身份和追踪关联，不意味着两个模型请求拥有同一份 messages。子图的 checkpoint 坐标也不能简单重新设置成父图根坐标，否则会破坏流隔离。

### 5.4 第四步：子智能体调用工具、形成报告

例如 Web Analyst 返回模式、item_selector、field_selectors、detail_fields、分页证据和不确定项。QA/Repair 返回失败解释和修复建议。

建议的分析结果结构如下，仅为讲解示例，不是已有网站预设：

~~~json
{
  "mode": "http_html",
  "selector_type": "xpath",
  "item_selector": "//section[@id='news-list']/article",
  "field_selectors": {
    "title": ".//h2/a",
    "url": ".//h2/a",
    "content": "//div[@id='article-content']"
  },
  "field_attributes": {"url": "href"},
  "detail_fields": ["content"],
  "detail_link_selector": ".//h2/a",
  "uncertainty": ["第二页详情模板尚未验证"]
}
~~~

实际参数必须由目标网页验证得到。不能直接拿这个示例给真实网站使用。

子角色最终输出仍主要是字符串报告。它不直接把整份子状态对象合并进主状态，也不自动更新另一个子角色的上下文。

### 5.5 第五步：报告成为 ToolMessage，回到 Lead

源码：[task_tool.py](../deer-flow/backend/packages/harness/deerflow/tools/builtins/task_tool.py)，L603—L642。以下代码按真实返回形状缩略，省略状态格式化和 additional_kwargs 构建：

~~~python
def task_result_excerpt(tool_call_id, content, metadata):
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    name="task",
                    additional_kwargs=metadata,
                )
            ]
        }
    )
~~~

tool_call_id 将结果与原 task 工具调用配对。additional_kwargs 还可携带子角色状态、stop_reason、模型名、用量、工具回执和验收结果等。

这个 Command 更新的是“主图中的 task 工具结果消息”，不要与子图直接使用 Command.PARENT 做任意共享状态写入混为一谈。

框架机制可对照 [LangGraph 官方工具状态更新说明](https://docs.langchain.com/oss/python/langgraph/use-graph-api)：工具返回 Command 更新消息状态时，需要有匹配的 ToolMessage。项目的实际行为仍以本地实现为准。

### 5.6 子角色之间怎么传消息

当前是：

~~~text
web-analyst 的报告
    → Lead 的 task ToolMessage
    → Lead 选择必要证据，写入下一次 task.prompt
    → qa-repair 的 HumanMessage
~~~

不是 Web Analyst 直接给 QA/Repair 发私信。Lead 也不会自动把所有 Web Analyst 内部探针结果、全部推理过程和完整聊天转发给 QA/Repair。

### 5.7 同步还是异步

内部执行是异步的，但普通 task 工具并不是“派出去马上返回一个 ID，让主模型不断轮询”。

task 工具在后端等待子任务，并按约 5 秒间隔检查后台状态，期间发送 task_started、task_running、task_completed 等事件；最终把结果一次回写主模型。300 秒子任务超时另有后端轮询安全缓冲。

需要区分四种 ID：

| ID | 用途 |
|---|---|
| thread_id / run_id | 用户会话、当前一次运行的归属 |
| task 的 tool_call_id | 父模型工具调用、返回消息、UI 子任务卡片的关联 |
| SubagentExecutor 的 execution_id | 后端控制这一次子智能体执行的内部 ID |
| AutoSpider task_id / execution_id / draft_id | 真正采集计划、采集执行实例和草稿，属于另一套业务对象 |

两个都叫 execution_id，不代表它们是同一个东西。

### 5.8 子角色报告是否可信

当前 verification.receipts_enabled=true、receipts_render_mode=delegation_only、judge_enabled=false。

子角色有工具回执账本和报告约定；框架可以检查报告引用是否与实际工具执行证据相符，还支持部分可判定的 acceptance_criteria。它们提高可追溯性，但不证明报告中的所有自然语言判断都正确，更不能替代 AutoSpider 的数据 QA。当前没有启用额外 LLM Judge。

<a id="memory"></a>

## 6. 它们共享记忆吗

准确答案是：部分持久资源可复用，但不共享一份无限、实时、全知的“共同记忆”。

| 状态/记忆 | 保存在哪里 | 是否共享 | 当前边界 |
|---|---|---|---|
| Lead 对话历史 | LangGraph messages 与 checkpoint | Lead 在会话内延续 | 子角色不自动拿到全量历史 |
| 子角色内部上下文 | 本次子图 messages | 默认不直接给其他角色 | 最终报告经 task 返回；子图 checkpointer=False |
| AutoSpider 站点记忆 | data/site_memory.db | 获准工具可访问同一库 | Lead、Web Analyst 的白名单有查询；QA/Repair 没有直接记忆查询工具 |
| DeerFlow 用户长期记忆 | DeerMem 用户范围存储 | 按用户及 agent bucket 组织 | 主角色可获注入；当前原生子角色链不自动继承主角色记忆注入 |
| 输出文件、草稿、任务记录 | runtime/playwright 及线程工作目录 | 通过获准工具和句柄读取 | 有文件不等于模型自动看到；还需路径权限与数据读取 |
| 浏览器 probe session | 某个 MCP 子进程的内存对象 | 不能保证跨角色延续 | session_id 不是持久化工作流 ID |

### 6.1 AutoSpider 的站点记忆

SiteMemoryRepository 使用 SQLite，主要表包括 sites、pages、selectors、workflows、executions、failures。

它保存页面规则、选择器、执行模式、计划及验证记录，不是通过训练更新大模型权重，也不是自动记住之前全部聊天。

典型路径：

~~~text
memory_lookup_site(url)
    → 精简元数据、字段、模式与 workflow_id
crawl_reuse_workflow(workflow_id, 新 URL / 限制)
    → 服务端读取原始任务
    → 克隆、适配与重新校验
    → 必要样本 QA
    → 全量与完整性检查
~~~

普通页面的部分查询参数变化或字段子集可以复用；新路径、新字段、固定 API 参数等不一定能复用。build_reused_crawl 会拒绝可能继续抓取旧频道数据的适配。

正常自动提交需要最终 PASS、全量阶段和完整验证。入口 memory_commit_validated 还检查报告状态及已有覆盖证书；高级接口接收报告对象，不应把一个外部传入的 PASS 字符串当作不可伪造的独立证明。

### 6.2 DeerFlow 的长期用户记忆

当前 memory.enabled=true、mode 默认 middleware、manager_class 默认 deermem、injection_enabled=true。

默认 DeerMem 布局为：

~~~text
{DEER_FLOW_HOME}/users/{user_id}/memory.json
{DEER_FLOW_HOME}/users/{user_id}/agents/{agent_name}/facts/.../{fact_id}.md
~~~

memory.json 主要保存共享摘要和修订信息，具体事实保存为 agent bucket 下的 Markdown 文件。storage_path 等配置可以改变默认位置。

本项目 Windows 启动脚本把 DEER_FLOW_HOME 指向 deer-flow/.deer-flow，因此不要把上游默认的 backend/.deer-flow 当作本机唯一位置。

主角色的 DynamicContextMiddleware 可注入日期和长期记忆；MemoryMiddleware 可把过滤后的用户与最终助手消息排入后台记忆处理。

当前子角色使用的是日期专用 SubagentDateContextMiddleware，而不是直接继承主角色的 DynamicContextMiddleware；子角色摘要还设置 skip_memory_flush=True，避免把内部工作对话写进父会话记忆。

### 6.3 站点库与用户记忆的隔离不同

DeerFlow 用户记忆有 user_id、agent_name 范围；但本项目 MCP 配置把 AutoSpider 站点库和 runtime 根目录指向固定绝对路径。

因此不能声称“每个用户的站点工作流已经独立隔离”。同一个本地库可被多个会话的 MCP 进程访问；权限、租户隔离和并发一致性需要分别设计，不能因为 DeerFlow 会话隔离就自动推导出来。

## 7. 上下文如何处理

### 7.1 主智能体上下文

包括框架 system、SOUL、当前用户需求、已有 AI/Tool 消息、必要动态上下文、摘要、委派账本和已激活技能引用。

ThreadState 的关键字段包括：

| 字段 | 用途 |
|---|---|
| messages | 对话与工具结果 |
| summary_text | 压缩后的历史摘要 |
| delegations | 委派记录及结果摘要 |
| skill_context | 已加载技能的名称、路径与描述引用，不是全文永久副本 |
| promoted | 按工具目录哈希记录的延迟工具推广状态 |
| sandbox / thread_data | 工具使用的线程工作环境 |
| uploaded_files / artifacts | 输入附件与交付物索引 |

### 7.2 子智能体上下文

每次 task 建立新消息容器，自身角色 prompt 与任务 HumanMessage 是起点。框架传入身份与资源信息，不复制父 messages。

源码创建子图时：

~~~python
# 来源：SubagentExecutor._create_agent，节选。
agent = create_agent(
    model=model,
    tools=bound_tools,
    middleware=middlewares,
    system_prompt=None,
    state_schema=ThreadState,
    checkpointer=False,
)
~~~

checkpointer=False 表示当前普通子角色不作为可恢复的长期对话图运行。它不否认后端会记录步骤事件和最终报告，也不否认共享 runtime 中存在业务 thread_id。

### 7.3 当前摘要配置

~~~yaml
summarization:
  enabled: true
  model_name: null
  trigger:
    - type: tokens
      value: 32000
  keep:
    type: messages
    value: 10
  trim_tokens_to_summarize: 15564
~~~

达到触发条件时压缩旧消息，保留近期消息并生成 summary_text。32000 是上下文压缩触发条件，不是整次运行的累计 Token 上限；keep=10 是消息策略，也不是“保留十轮完整对话”的保证。

主、子角色均接入摘要；DurableContextMiddleware 将摘要、委派和技能引用投影到下一次模型请求，减少关键工作状态随原始工具消息一起被压掉的风险。

摘要仍可能损失细节。URL、字段、页数、执行 ID 和失败证据应该通过清晰任务描述及持久化对象保持，不能全靠自然语言摘要。

### 7.4 工具返回如何限制上下文增长

1. HTTP overview 返回有界结构证据，不日常回传完整 DOM。
2. 选择器测试针对完整响应执行，但返回计数和少量样例；有范围/数量预算时不等于无限遍历所有候选。
3. 采集结果全文写文件，Lead 优先读取服务端 QA 与摘要。
4. 超大的框架工具结果可被外置到 .tool-results，模型只收到短描述和引用。
5. crawler_compact 缩短通用提示词，预绑定六个常用工具，其余 schema 按需推广。
6. 工具推广回执、角色报告和记忆仍是数据，不能提升为可信系统指令。

### 7.5 为什么不能只给另一个角色传浏览器 session_id

MCP 会话池以服务名及 user_id:thread_id 分组，但持久 stdio 会话还与事件循环绑定。

源码节选：

~~~python
# mcp/tools.py：会话范围
thread_id = _extract_thread_id(runtime)
user_id = resolve_runtime_user_id(runtime)
scope_key = f"{user_id}:{thread_id}"
~~~

session_pool.py 会在发现会话属于不同/已关闭事件循环时淘汰旧会话。Lead 和原生子角色的执行循环不同；AutoSpider BrowserInspector 的 probe 会话又保存在 MCP 进程内存中。

因此，跨角色切换时不能假定某个 session_id 对应的活浏览器仍在。应传有界 DOM/网络/选择器证据，或传可重新读取的持久化任务、草稿、文件和工作流句柄。需要新探针时按策略重新打开，不重启已有采集任务。

<a id="capabilities"></a>

## 8. Tools、MCP、Skills 分别是什么

| 概念 | 在本项目中的含义 | 不是 |
|---|---|---|
| Tool | 模型可请求调用的一个有名称、参数 schema 和实现的能力 | 不是另一个模型本身 |
| MCP | 让 DeerFlow 发现并调用外部工具服务的协议/适配通道 | 不是主从智能体聊天协议 |
| Skill | 场景说明、工作方法及 allowed-tools 等声明 | 不是默认自动执行的 Python 程序，也不是独立智能体 |
| Prompt | 角色与框架行为要求 | 不等于不可绕过的程序安全边界 |

### 8.1 Lead 的普通工具

当前 profile 中普通工具候选白名单：

| 工具 | 用途 |
|---|---|
| task | 委派两个获准专家 |
| ask_clarification | 缺少关键选择或需要新授权时请求澄清 |
| present_files | 展示真实输出文件；仅能展示框架允许的输出路径 |
| read_file | 有界读取技能/文件等 |
| describe_skill | 按需获取可见技能信息 |

tool_search 是延迟 MCP 发现机制生成的辅助工具，不应因为它没有写在上述普通工具列表里就断言不可用。

普通 DeerFlow 全局可以配置 bash、write_file、str_replace、web_search、web_fetch、image_search、ls、glob、grep 等；它们不是 crawler-lead 当前普通工具白名单中的日常能力。不能把“框架安装了某工具”直接写成“爬虫角色能随意调用它”。

### 8.2 Lead 预绑定的六个 MCP 工具

~~~yaml
preload_tools:
  - autospider_memory_lookup_site
  - autospider_crawl_reuse_workflow
  - autospider_probe_http
  - autospider_probe_http_select
  - autospider_crawl_draft_create
  - autospider_crawl_draft_execute
~~~

常规采集直接调用这六个工具，不需要先搜索全套 schema。

### 8.3 Web Analyst 的业务工具白名单

~~~text
autospider_probe_open
autospider_probe_http
autospider_probe_http_select
autospider_probe_snapshot
autospider_probe_network
autospider_network_jsonpath_test
autospider_probe_close
autospider_selector_test
autospider_memory_lookup_site
~~~

### 8.4 QA/Repair 的业务工具白名单

~~~text
autospider_execution_result_summary
autospider_qa_validate_execution
autospider_execution_logs
autospider_selector_test
autospider_probe_snapshot
autospider_qa_validate
~~~

子角色在工具装配后还可能获得 tool_search、describe_skill 等框架发现辅助能力；这些辅助工具不会把其业务工具白名单扩展成整套 MCP。

当前子角色业务名单没有 read_file、bash、write_file，也没有 execution_start / task_register / crawl_draft_execute。

### 8.5 实际可调用能力怎样确定

可以概括为：

~~~text
全局可用工具
  → 身份/授权过滤
  → 角色工具 allowlist/denylist 或 profile 过滤
  → MCP 延迟目录与 schema 推广
  → 活动 Skill 的 allowed-tools 过滤
  → 调用时授权及服务端参数、网络和执行策略检查
~~~

预绑定、被搜索到、显示在文档中，都不等于拥有最终执行权限。技能被配置为 enabled 也不等于其 allowed-tools 已经自动接管全部工具。

## 9. 当前 MCP 的部署与消息

磁盘运行配置摘录（仅保留非秘密字段）：

~~~json
{
  "mcpServers": {
    "autospider": {
      "enabled": true,
      "type": "stdio",
      "command": "D:\\conda_envs\\mytorch\\python.exe",
      "args": ["-m", "services.autospider_mcp.server"],
      "tool_call_timeout": 1800,
      "session_init_timeout": 60,
      "env": {
        "AUTOSPIDER_ENGINE": "native",
        "AUTOSPIDER_RUNTIME_ROOT": "D:\\VsCode\\syslabe\\AutoSpider\\runtime\\playwright",
        "AUTOSPIDER_MEMORY_DB": "D:\\VsCode\\syslabe\\AutoSpider\\data\\site_memory.db"
      }
    }
  }
}
~~~

DeerFlow 按需管理该 Python MCP 子进程，通过 stdin/stdout 交换协议消息。不是要求用户手工打开 EasySpider，也不是给三个角色分别开一个 8074 服务。

服务注册的原始工具名如 probe_http；DeerFlow 默认加服务前缀，模型看到的是 autospider_probe_http。

下面仅展示 MCP tools/call 的消息形状，省略初始化、能力协商和实际返回内容：

~~~json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "probe_http",
    "arguments": {"url": "https://example.org/news"}
  }
}
~~~

“传 JSON-RPC”描述的是工具服务边界；“task.prompt 和 task ToolMessage”描述的是智能体委派边界。

<a id="mcp-inventory"></a>

## 10. AutoSpider MCP 全部工具清单

当前 services/easyspider_mcp/server.py 中登记了 28 个 MCP 工具。下表使用服务端原名，模型调用时加 autospider_。

### 10.1 结构与网络探针：8 个

| 工具 | 主要用途 | 关键输入 |
|---|---|---|
| probe_open | 打开只读浏览器分析页面 | url、域名与 robots 策略 |
| probe_http | 无浏览器检查 HTML/JSON；默认紧凑 overview | url、method、请求参数、mode |
| probe_http_select | 在完整 HTML 响应上测试 XPath/CSS | url、selectors、scope_selector、attributes |
| probe_snapshot | 有界页面结构与元素信息 | session_id、mode |
| probe_network | 有界且脱敏的 XHR/fetch 证据 | session_id、limit |
| network_jsonpath_test | 在捕获的 JSON 上测试安全 JSONPath | session_id、path、request_index |
| probe_close | 释放分析浏览器 | session_id |
| selector_test | 在浏览器页面测试 XPath/CSS | session_id、selector_type、selector |

### 10.2 常规紧凑流程：3 个

| 工具 | 主要用途 |
|---|---|
| crawl_draft_create | 从已验证选择器和分页等紧凑参数生成类型化草稿 |
| crawl_draft_execute | 样本、QA、全量、最终 QA 与正常验证后记忆提交 |
| crawl_reuse_workflow | 复用已验证工作流，服务端适配而不让模型重写全部计划 |

### 10.3 高级计划与原子执行：4 个

| 工具 | 主要用途 |
|---|---|
| crawl_plan_build | 从完整 CrawlSpec/PageModel 构建计划 |
| task_compile | 将计划编译为本项目执行清单 |
| task_compile_register | 编译并注册，避免模型手工重写清单 |
| crawl_execute_validated | 编译、注册、执行、等待、验证的一体化高级入口 |

task_hash 是规范化数据的无密钥 SHA-256 一致性校验，不是数字签名、身份认证或不可伪造证明。部分源注释使用 signed 一词，汇报时应使用准确表述。

### 10.4 任务生命周期与结果：8 个

| 工具 | 主要用途 |
|---|---|
| task_register | 注册编译后的任务 |
| task_get | 读取任务 |
| execution_create | 为任务建立执行实例 |
| execution_start | 启动已创建的执行实例 |
| execution_logs | 读取状态、日志、错误及有限等待 |
| execution_stop | 停止执行 |
| execution_result | 返回有限记录 |
| execution_result_summary | 返回状态、总数、文件与有界预览 |

### 10.5 QA：2 个

| 工具 | 主要用途 |
|---|---|
| qa_validate | 验证直接传入的记录 |
| qa_validate_execution | 在服务端验证某次执行结果，不把全文塞进模型上下文 |

### 10.6 站点记忆：3 个

| 工具 | 主要用途 |
|---|---|
| memory_lookup_site | 查询精简站点/工作流元数据 |
| memory_get_workflow | 按 ID 读取完整工作流；高级修复时按需使用 |
| memory_commit_validated | 检查报告后保存选择器与计划 |

<a id="skills"></a>

## 11. 六个爬虫 Skills

### 11.1 各自职责及声明的工具范围

表中仅省略共同的 autospider_ 前缀；task 为框架工具。

| Skill | 职责 | allowed-tools 声明 |
|---|---|---|
| intelligent-crawling | 端到端编排与业务约束 | task，加上上述全部 28 个 AutoSpider 工具 |
| autospider-workflow | 计划、执行、结果、复用 | memory_lookup_site、crawl_reuse_workflow、probe_http_select、crawl_draft_create、crawl_draft_execute、crawl_plan_build、task_compile、task_compile_register、crawl_execute_validated、task_register、task_get、execution_create、execution_start、execution_logs、execution_stop、execution_result、execution_result_summary、qa_validate_execution |
| webpage-analysis | 列表/详情/分页证据分析 | probe_open、probe_close、probe_snapshot、probe_http、probe_http_select、probe_network、network_jsonpath_test、selector_test、memory_lookup_site |
| selector-engineering | 选择器发现、测试与局部修正 | probe_http_select、selector_test、probe_snapshot、network_jsonpath_test |
| crawl-validation | 质量验证与修复诊断 | qa_validate、qa_validate_execution、execution_result、execution_result_summary、execution_logs、selector_test、probe_snapshot |
| transport-selection | 在证据支持下选择三种模式 | probe_http、probe_http_select、probe_open、probe_snapshot、probe_network、network_jsonpath_test、selector_test、probe_close |

这些是声明范围，不是给所有角色授予所有工具。

### 11.2 角色声明与父子技能交集

当前所有六个集成技能在 extensions_config.json 中 enabled=true，但 Lead 自身仅配置四个可见技能。

| 角色 | 角色配置声明 | 从当前 crawler-lead 用原生 task 委派后的技能可见范围 |
|---|---|---|
| crawler-lead | intelligent-crawling、autospider-workflow、crawl-validation、transport-selection | 这四个 |
| web-analyst | webpage-analysis、selector-engineering、transport-selection | transport-selection |
| qa-repair | crawl-validation、selector-engineering | crawl-validation |

原因在 task_tool.py 的真实合并函数：

~~~python
def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    if parent is None:
        return child
    if child is None:
        return list(parent)
    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]
~~~

所以“分析角色声明了三个技能”与“这次从爬虫主角色委派后能看到三个技能”不是同一件事。这个差异不会自动删除其未激活技能时的业务工具白名单，但会影响技能发现和可激活范围。

### 11.3 技能如何加载和生效

1. enabled 与角色 skills 决定哪些技能元数据可见。
2. deferred_discovery=true 时优先展示技能名称索引，describe_skill 按需返回更多信息。
3. 技能正文不是全部自动装进每次请求；严格的 /skill-name 形式或被识别的 SKILL.md 读取可触发激活。
4. 激活后，SkillToolPolicyMiddleware 对模型可见工具和实际调用应用 allowed-tools。
5. 多个自主读取技能通常按活动集合处理；显式 slash 激活具有更明确的限制语义。

当前子角色没有普通 read_file 业务权限，不能因为它看得到技能名称，就声称它会自动读取所有技能全文。框架激活、发现辅助工具和业务工具是不同层。

例如，若 Lead 只显式激活 transport-selection，该技能不声明 task 或草稿执行工具；不能承诺这个受限运行仍可自由委派并执行全流程。日常紧凑路径不要求先把所有技能读一遍。

<a id="execution"></a>

## 12. 智能体最终怎样完成爬取

### 12.1 常规任务不是让模型临时写完整 Python 爬虫

~~~text
自然语言需求
  → 紧凑字段、选择器、分页与模式证据
  → CrawlSpec + PageModel
  → CrawlPlan 中间表示
  → autospider.task/v1 执行清单
  → 固定 Python 执行器
  → 数据文件、QA 与工作流记忆
~~~

核心模型：

| 对象 | 表达什么 |
|---|---|
| CrawlSpec | 用户目标、字段、遍历限制、执行与输出要求 |
| PageModel | 页面类型、选择器、详情与分页结构证据 |
| CrawlPlan | OPEN_URL、LOOP、EXTRACT 等确定操作组成的执行计划 |
| ValidationReport | 质量结果、指标、问题和覆盖诊断 |
| RepairPatch | 对计划的受限修改建议 |

执行器是 Python 编写的，但每次任务通常生成的是配置化计划，不是新建一份任意网站专用 .py 再交给 shell 运行。

### 12.2 三种执行模式

| 模式 | 适用证据 | 执行方式 |
|---|---|---|
| http_api | 公开、稳定、安全且覆盖所需字段的 JSON 接口 | httpx GET/POST + 安全 JSONPath |
| http_html | 原始 HTML 已包含目标数据 | httpx GET/POST + lxml XPath/CSS |
| playwright | 必须渲染、交互、滚动或使用策略允许的浏览器状态 | Playwright 操作浏览器 |

当前提示词要求优先考虑已验证 API、原始 HTML，再考虑浏览器。这不是已证明的全局最优成本优化器，也不是不经验证的自动接口逆向。

真实执行分发代码节选：

~~~python
def _executor_for(self, execution_id: int):
    execution = self.repository.get_execution(execution_id)
    engine = execution["task"].get("engine")
    if engine == "playwright":
        return self.playwright
    if engine in {"http_html", "http_api"}:
        return self.http
    raise AdapterError(f"unsupported AutoSpider engine: {engine!r}")
~~~

来源：[HybridExecutor](../packages/executor/hybrid.py)，约 L27。

### 12.3 与 EasySpider 的关系

当前 AUTOSPIDER_ENGINE=native。执行链使用本项目的 Compiler、TaskRepository、HybridExecutor、HttpExecutor 和 PlaywrightExecutor，不需要启动 EasySpider，也不调用其桌面程序或 8074 接口。

services/autospider_mcp/server.py 仍从本项目内部的 services/easyspider_mcp/server.py 导入实现。旧目录名、数据库 easyspider_task_id 字段和兼容适配器不是当前运行上依赖上游仓库的证据。

这项运行依赖结论不替代逐模块源码来源、开源许可和专利审查。

## 13. QA 与修复机制的真实边界

程序 QA 与 QA/Repair 模型是两个层次：

- 程序 QA：对记录执行可重复规则检查，返回指标、问题和覆盖证据。
- QA/Repair：在 FAIL 后解释有限证据并建议修复，不作为日常 PASS 的必要模型步骤。

当前代码已包含按观测到的 URL 路由分层的字段覆盖分析，以及 coverage_certificate、evidence_requests 等诊断。模板中的 QA 提示词也增加了“失败分层不能被总体平均值掩盖”的要求，但已安装的旧 QA 文本尚未同步，见第 16 节。

当前 select_sample_indices 默认支持 boundary_spread；它按位置覆盖候选集边界，不是自动识别所有未见过的详情模板。样本通常仍受首列表页等限制，不能声称抽五条就证明全站布局一致。

程序的字段非空、重复、格式和路由覆盖检查，不等于证明新闻事实真实、正文语义完整或所有页面都已访问。

正常自动提交的关键门控可以缩略为：

~~~python
# 按 server.py 的实际条件缩略；不是独立可运行入口。
if (
    report is not None
    and report.get("status") == "PASS"
    and result.get("phase") == "full"
    and result["validation_complete"]
    and result.get("code") != "policy_blocked"
):
    committed = memory_commit_validated(
        page, plan, spec, result["validation"], task_id=result["task_id"]
    )
~~~

服务端结果读取/自动 QA 有最多 1000 条的范围限制；超过时会报告验证不完整，不应宣称全量 QA 已完成或自动保存为完整验证记忆。

超时后可用相同 draft_id 续查原执行阶段；终态结果可返回缓存。它不是记录级 checkpoint，也不承诺多进程并发同 ID 的 exactly-once。

## 14. 模型、API、Thinking 与 Token

### 14.1 一个模型可以支撑三个角色

当前两个子角色均 model: inherit。磁盘配置中的主模型为 glm-5.3；没有另行选用模型时，子角色继承父运行解析后的模型。

一个 API 可以支持多个角色的独立请求上下文，不要求三个 API Key。角色分离来自不同提示词、任务、工具和状态，不必来自不同模型。

以后可将子角色设置成不同模型，但那需要相应配置和能力验证，并不自动降低费用或延迟。

### 14.2 Thinking 的真实配置

Lead 的 thinking_enabled=false 被锁定；子模型构建也传 thinking_enabled=False。但 GLM-5.3 的提供商分支在“开”和“关”两种配置里都映射到：

~~~yaml
extra_body:
  thinking:
    type: enabled
  reasoning_effort: low
~~~

因此不能汇报为“GLM-5.3 已完全关闭思考”。准确说法是：角色使用低消耗配置；当前 GLM 模型要求思考开启，使用 low。

### 14.3 不同预算不要混淆

| 控制 | 当前磁盘配置/源码默认 | 意义 |
|---|---|---|
| Lead 累计 Token 预算 | crawler-lead 覆盖为 enabled=true、200000 | 当前 Lead run 的累计用量防护，不是单次上下文长度 |
| 全局普通 token_budget | enabled=false | 不覆盖已单独启用的 crawler-lead 限制 |
| 子角色 Token 预算 | 没有显式配置时，开启摘要的源码默认约 1000000/子运行 | 独立子角色防护，不是继承 Lead 的 200000 |
| 摘要触发 | 32000 tokens | 上下文压缩时机 |
| Web Analyst / QA max_turns | 12 / 10 | 子运行模型回合限制，不是每回合只能调用一个工具 |
| 子角色 timeout | 各 300 秒 | 子智能体运行时间限制 |
| 普通 task 总委派 | 每 Lead run 2 次 | 防止不断创建新子任务 |
| 进程子任务容量 | max_running=3、max_queued=64 | 后端共享容量，不保证当前爬虫每次三角色并行 |
| MCP 调用超时 | 1800 秒 | 一次工具调用等待上限，不是模型执行时间承诺 |

TokenUsageMiddleware 可把子角色的终态用量归入对应的父调用，Lead 的预算会依据已归入消息的 usage 统计。该归因不是对正在后台运行的子任务逐 token 同步抢占；单次响应和子角色运行可能在检查前越过阈值。

摘要、长期记忆抽取、标题等框架辅助调用也可能消耗模型资源。不要只统计输出文本长度来解释全部费用；逻辑 Token 预算也不等于提供商最终账单。

<a id="boundaries"></a>

## 15. 安全、错误与可观测性

### 15.1 服务端错误不是都交给模型猜

AutoSpider MCP 入口有参数校验和结构化失败回执，包含 status、code、parameter、field_errors、retryable、next_action 等信息。

| 情况 | 处理原则 |
|---|---|
| XPath/CSS 语法不支持 | 定位具体字段；保留其他有效诊断；有界修正，不切换模式盲试 |
| 合法选择器匹配 0 条 | 语法合法但没有目标，不等于 QA 已通过 |
| JSON/HTML 格式不符 | 作为传输/结构证据重新判断，不作为绕过策略的理由 |
| 临时网络故障 | 仅按回执建议对适合的请求有限重试；不自动重放 POST |
| 临时 probe session 失效 | 有界重新开分析页面，不重启采集执行 |
| policy_blocked | 停止相关执行路径，报告不完整；不通过别的模式绕过 |
| 内部错误 | 停止并报告实际诊断，不靠重复调用碰运气 |

robots、域名、SSRF/私网、跨站导航等策略在服务端执行；提示词也要求遵守。可信本地代理的 verified_public Fake-IP 处理不等于关闭私网防护。

### 15.2 如何证明某次真的使用了多个智能体

配置了三个角色只能证明“具备三角色能力”，不能证明某次任务三个都参与。

检查证据应包括：

1. Lead 是否实际产生 task 调用，以及 subagent_type。
2. task_started / task_running / task_completed / task_failed 等事件。
3. 匹配 tool_call_id 的最终 ToolMessage 与状态。
4. 当前 run 的 delegations 账本、子模型用量和必要工具回执。
5. AutoSpider 采集 execution_id、实际结果与程序 QA。

UI 卡片主要是观测通道；实际数据是否通过 QA 仍应看采集服务返回的执行结果。

## 16. 本次审阅发现的配置差异与限制

本次只记录，不修改运行系统。

| 项目 | 审阅结果 | 汇报时的准确说法 |
|---|---|---|
| Lead 安装配置 | 已检查的 default 与已登录用户副本和模板一致 | 两处都配置三个爬虫角色的同一入口 |
| Web Analyst prompt | config.yaml 中正文与模板一致 | 当前分析角色已使用该模板内容 |
| QA/Repair prompt | config.yaml 中正文比模板旧，缺少新增分层覆盖/有界 evidence_requests 要求 | 不能把模板新增指令全部描述为已装入角色 |
| crawl-validation Skill | 两个已检查用户的安装正文与模板不同，allowed-tools 相同 | 服务端 QA 已有新机制，但安装的技能说明尚未同步 |
| 子角色 skills | 父子白名单取交集 | Web Analyst 当前有效一个；QA/Repair 当前有效一个，而不是声明的三/两个 |
| MCP routing 中文关键词 | 磁盘配置存在乱码文本 | 英文关键词与六工具预绑定仍可描述；中文自动发现效果不能据模板保证 |
| MCP 临时会话 | 事件循环绑定、进程内 probe 状态 | 不能保证跨角色复用 session_id |
| 站点库/运行根目录 | 配置成共享绝对路径 | 不等同于已完成多用户租户隔离 |
| durable MCP runtime | 全局 mcp_tasks.enabled=true，但当前 autospider 没有 task_toolsets 绑定 | 普通工具调用没有自动变成框架 durable MCP 远程任务 |
| durable batch | subagent_batches.enabled=false | 当前主路径是普通 task，不是已启用持久批量多智能体调度 |
| 调度与多智能体 | scheduler.enabled=true | 定时触发是可用配置；不证明某个定时任务已创建或实际运行 |

后续完善优先级建议：

1. 先确认并同步 QA prompt 与技能安装副本。
2. 按预期重新设计父子技能可见范围，而不是简单放开全部工具。
3. 明确主/子 Token 预算和每次 run 的委派次数之间的关系。
4. 把可持久化证据与临时浏览器会话分开传递。
5. 多用户部署前单独处理站点记忆、任务目录与权限隔离。

这些是下一步建议，不表示本次已经实施。

## 17. 不应在汇报中夸大的能力

- 三个角色不是三个必定同时工作的独立服务。
- 不共享全部聊天、推理过程或模型权重。
- 更多子角色不必然更快、更省 Token。
- 工具白名单、技能可见、技能激活和实际授权是四个不同问题。
- 站点指纹目前不能被描述为完整的模糊页面漂移学习系统；紧凑草稿的 fingerprint 主要是配置布局哈希。
- SHA-256 校验不能称为不可伪造签名。
- 程序 QA 不等于事实核验或全文语义正确性的形式证明。
- boundary_spread 抽样不证明发现所有异构模板。
- 草稿阶段续行不等于记录级断点续爬；普通 task 的子图本身不恢复长期对话。
- CrawlStateMachine 的定义不能直接证明所有生产工具入口都由统一形式化状态机驱动。
- 独立于 EasySpider 运行不等于所有功能已达到 EasySpider 全量能力对等。

<a id="sources"></a>

## 18. 源码定位索引

下列行号为本次审阅定位，后续改代码可能变化；优先按函数名查找。链接相对于本文所在的 markdown 目录。

| 主题 | 文件与入口 |
|---|---|
| 项目定位与日常流程 | [README.md](../README.md)，开头架构及“站点复用与 Token 预算” |
| Lead 配置 | [crawler-lead/config.yaml](../deerflow/agents/crawler-lead/config.yaml)，tool_names / preload_tools / skills / allowed_subagents |
| Lead 角色要求 | [crawler-lead/SOUL.md](../deerflow/agents/crawler-lead/SOUL.md)，Compact default / Boundaries |
| 分析角色模板 | [web-analyst/SYSTEM.md](../deerflow/agents/web-analyst/SYSTEM.md) |
| 修复角色模板 | [qa-repair/SYSTEM.md](../deerflow/agents/qa-repair/SYSTEM.md) |
| 实际模型及子角色文本 | [deer-flow/config.yaml](../deer-flow/config.yaml)，models / memory / subagents / summarization |
| 实际 MCP 配置 | [extensions_config.json](../deer-flow/extensions_config.json)，mcpServers.autospider / skills；文件可能含秘密，演示时只展示脱敏字段 |
| 模板安装流程 | [bootstrap.py](../scripts/bootstrap.py)，合并配置、复制用户技能和 Lead 配置 |
| 主图装配 | [lead_agent/agent.py](../deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py)，build_middlewares、角色工具选择、约 L1164—L1230 |
| 紧凑 system 模板 | [lead_agent/prompt.py](../deer-flow/backend/packages/harness/deerflow/agents/lead_agent/prompt.py)，apply_crawler_prompt_template，约 L1049 |
| task 入口与返回 | [task_tool.py](../deer-flow/backend/packages/harness/deerflow/tools/builtins/task_tool.py)，_merge_skill_allowlists L592、_task_result_command L603、task_tool L646、execute_async L924 |
| 子图与上下文 | [subagents/executor.py](../deer-flow/backend/packages/harness/deerflow/subagents/executor.py)，_create_agent L926、_build_initial_state、HumanMessage L1263、runtime context 约 L1470 |
| 子角色中间件 | [tool_error_handling_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py)，build_subagent_runtime_middlewares L343 |
| 子预算默认与覆盖 | [subagents_config.py](../deer-flow/backend/packages/harness/deerflow/config/subagents_config.py)，default_subagent_token_budget、get_token_budget_for |
| 累计 Token 防护 | [token_budget_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/token_budget_middleware.py) |
| 用量归因 | [token_usage_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/token_usage_middleware.py) |
| 上下文摘要与持久引用 | [summarization_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py)、[durable_context_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py) |
| 日期及记忆注入 | [dynamic_context_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py) |
| 用户长期记忆定义 | [memory/AGENTS.md](../deer-flow/backend/packages/harness/deerflow/agents/memory/AGENTS.md)、[memory_config.py](../deer-flow/backend/packages/harness/deerflow/config/memory_config.py) |
| Skill 激活与工具策略 | [skill_activation_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/skill_activation_middleware.py)、[skill_tool_policy_middleware.py](../deer-flow/backend/packages/harness/deerflow/agents/middlewares/skill_tool_policy_middleware.py) |
| MCP 工具装配与作用域 | [mcp/tools.py](../deer-flow/backend/packages/harness/deerflow/mcp/tools.py)，约 L527—L532 |
| MCP 会话循环绑定 | [mcp/session_pool.py](../deer-flow/backend/packages/harness/deerflow/mcp/session_pool.py)，约 L273—L280 |
| AutoSpider 新服务入口 | [services/autospider_mcp/server.py](../services/autospider_mcp/server.py) |
| 28 个 MCP 工具实现 | [services/easyspider_mcp/server.py](../services/easyspider_mcp/server.py)，probe_http、crawl_draft_execute、memory_lookup_site 等 |
| 结构化工具错误 | [tool_errors.py](../services/easyspider_mcp/tool_errors.py)，AutoSpiderMCP / 失败回执 |
| 紧凑草稿与布局哈希 | [drafts.py](../packages/crawl_core/drafts.py)，build_crawl_draft、CrawlDraftRepository |
| 已验证工作流适配 | [reuse.py](../packages/crawl_core/reuse.py)，build_reused_crawl |
| 执行清单编译与校验 | [compiler/playwright.py](../packages/compiler/playwright.py)、[task_manifest.py](../packages/crawl_core/task_manifest.py) |
| 三模式分发 | [HybridExecutor](../packages/executor/hybrid.py)、[HttpExecutor](../packages/executor/http.py)、[PlaywrightExecutor](../packages/executor/playwright.py) |
| 站点数据库 | [site_memory/repository.py](../packages/site_memory/repository.py)，表结构及 commit_validated |
| 确定性 QA 与分层覆盖 | [validator/report.py](../packages/validator/report.py)、[validator/coverage.py](../packages/validator/coverage.py) |
| 六个技能全文 | [intelligent-crawling](../deerflow/skills/intelligent-crawling/SKILL.md)、[autospider-workflow](../deerflow/skills/autospider-workflow/SKILL.md)、[webpage-analysis](../deerflow/skills/webpage-analysis/SKILL.md)、[selector-engineering](../deerflow/skills/selector-engineering/SKILL.md)、[crawl-validation](../deerflow/skills/crawl-validation/SKILL.md)、[transport-selection](../deerflow/skills/transport-selection/SKILL.md) |

## 19. 常见答辩问题

**问：为什么是多智能体，不直接一个模型做完？**

答：复杂结构分析与质量修复可以拥有更窄的职责、提示词、工具和上下文，降低互相干扰；简单或有记忆任务仍由主角色直接完成。是否更高效需要实测，不预设多角色必然更好。

**问：它们互相聊天吗？**

答：当前是 Lead 通过 task 委派，子角色通过 ToolMessage 返回。两个子角色之间由 Lead 选择证据转交，没有原生的兄弟角色直接聊天通道。

**问：用了一个还是三个模型？**

答：当前子角色继承父模型，可以用一个提供商/API 支撑多个角色的独立上下文。一次任务实际发多少模型请求取决于委派、工具循环和框架辅助调用。

**问：它们共享记忆吗？**

答：不共享全部聊天；获准角色可读取统一站点库。DeerFlow 长期用户记忆与站点规则库是两套系统，子角色也不自动继承父模型的全部记忆注入。

**问：MCP 是智能体间通信吗？**

答：不是。MCP 是调用采集工具的接口；智能体委派走 DeerFlow 的 task 和消息状态更新。

**问：每次都生成 Python 代码吗？**

答：常规路径只生成紧凑规则与类型化计划，由固定 Python 执行器完成请求或浏览器操作。不是每个网站都临时生成并运行新的脚本。

**问：下一次同网页怎么复用？**

答：查到已验证且适用的工作流后，服务端克隆适配并重新验证；不是不检查参数、字段、路由变化就盲跑旧任务。

**问：现在最大的工程改进点是什么？**

答：统一模板和安装配置、明确父子技能范围、收紧并统一资源预算、处理临时会话边界，以及在多用户部署前完善持久数据的权限隔离。

---

汇报收束句：本系统的核心不是“让三个模型轮流聊天”，而是用职责分离和有界上下文辅助决策，让经过校验的计划交给可重复执行的采集服务，并以真实执行证据和质量门控支持复用。
