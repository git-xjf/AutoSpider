# AutoSpider：宿舍与工位两台电脑的 GitHub 协作

建立日期：2026-11-15。目标仓库：[git-xjf/AutoSpider](https://github.com/git-xjf/AutoSpider)，私有。

Git 保存已提交的文件版本、修改说明与差异；GitHub 接收推送后的提交。未提交、未推送的修改不会自动出现在另一台电脑。两台电脑使用同一个账号即可，不需要建立两个仓库。

## 1. 仓库管理范围

源码采用单仓库结构：

```text
AutoSpider/                 唯一日常 Git 工作区
├── packages/ services/     采集核心与 MCP
├── deerflow/               Agent、Skills、配置模板
├── deer-flow/              定制后的 DeerFlow 源码，普通目录而非 submodule
├── scripts/ tests/         启动、安装、实验脚本与测试
├── docs/ markdown/         文档、研究和专利材料
└── UPSTREAM.lock           已核对的上游版本，不是 AutoSpider 提交号
```

当前本机原 DeerFlow Git 元数据完整保留在 `.git/autospider-local-backups/deer-flow.git`，迁移前的文件哈希清单位于 `.research/github-setup-20261115/`。两处都是本机备份，不会推送。原来的源码定制与新增文件纳入本次 AutoSpider 初始快照；初始提交之前的项目开发过程不补造成历史提交。

`deer-flow/LICENSE` 保留上游许可，`deer-flow/UPSTREAM_VENDOR.json` 记录源码基线。后续在项目根目录执行 Git 命令即可；不要在子目录重新 `git init` 或将整个定制框架替换为最新上游。

以下内容不进入 Git：

| 内容 | 原因与处理 |
| --- | --- |
| `.env`、密钥、登录签名文件 | 每台电脑独立填写；只提交空值/占位的 example |
| `deer-flow/config.yaml`、实际 extensions 配置与备份 | 含本机模型设置和绝对路径；由模板与 bootstrap 生成 |
| `.deer-flow/`、会话与账号数据库 | 属于用户运行状态，另做一致性备份 |
| `data/`、`runtime/`、聊天回放 | 记忆库、采集结果、运行日志与历史材料单独管理 |
| `.venv`、`node_modules`、`.next`、缓存 | 每台电脑重新安装，不同步解释器和依赖文件夹 |
| `.vscode/`、`.workbuddy/` | 本机设置与助手私有历史，不随代码共享 |
| `EasySpider/`、二进制发行目录 | 非原生执行路径的必需项，按上游记录另行取得 |

部分研究文档链接到被排除的历史 `runtime/experiments/` 证据。这些材料需要另行备份/传输；仓库克隆包含研究源码与文档，但不代表自动包含全部旧实验产物或原机站点记忆。

## 2. 在另一台 Windows 电脑首次使用

先安装 Git、GitHub CLI、Node.js 22+、Conda/Python 和 uv。项目路径尚未确定时可选 `D:\Projects\AutoSpider`；路径不是固定要求，建议避免把新 clone 覆盖到旧压缩包目录。

在 PowerShell 登录，按网页流程选择 `git-xjf`：

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
```

第一次克隆：

```powershell
New-Item -ItemType Directory -Force -Path 'D:\Projects' | Out-Null
Set-Location -LiteralPath 'D:\Projects'
git clone https://github.com/git-xjf/AutoSpider.git
Set-Location -LiteralPath 'D:\Projects\AutoSpider'

git config --local user.name git-xjf
git config --local user.email '113340806+git-xjf@users.noreply.github.com'
git config --local pull.ff only
git config --local core.autocrlf false
git config --local commit.template .github/commit_template.txt
git status
```

以上邮箱是 GitHub noreply 提交邮箱，不需要把个人邮箱写到提交里。`git config --local` 只影响当前项目；每个 clone 各设置一次。

然后按照 [Work Buddy 运行交接指南](../markdown/AutoSpider_WorkBuddy_项目交接与运行指南.md) 重建两套 Python 环境、安装前端依赖，并用新机器的 AutoSpider 解释器运行 `scripts/bootstrap.py`。注意新 clone 没有实际 `config.yaml`、`.env` 或 extensions 配置：bootstrap 会从模板创建/合并配置，随后填写密钥并运行模型配置脚本，再启动 Gateway。

必须检查：

- AutoSpider Python 3.11 与 DeerFlow Python 3.12 分离。
- MCP command、PYTHONPATH、runtime 和 memory 路径指向新机。
- 主控/Skills 安装到真实登录 user_id。
- `scripts/start_deerflow_windows.ps1` 的解释器路径匹配新机，或按交接指南手动启动。
- 旧压缩包继续保留为备份；没有新修改时无需把它整体覆盖到 clone。

## 3. 每次开始工作

先到本项目目录，查看是否有未提交修改：

```powershell
git status
git pull --ff-only
```

先提交/妥善保存本机已有工作，再处理远端更新，不要通过删除工作区来消除提示。`--ff-only` 在两边历史分叉时会停止，防止无意间混合修改；遇到这种情况按下面的冲突步骤处理。

每次切换电脑，上一台先推送，下一台先拉取。两台电脑同时改代码也可以，但尽量不要同时改同一功能或同一文件。

## 4. 每次完成一项修改

完成代码、必要测试和对应文档后，查看差异并选择要提交的文件。下面是修改记忆模块时的提交格式示例，文件名和说明应匹配实际工作：

```powershell
git status
git diff
git add -- packages/site_memory/repository.py
git diff --cached --stat
git diff --cached
git commit -m 'fix(memory): bind failures to workflow versions' `
    -m '原因：失败日志未影响对应旧规则。修改：关联执行事件与版本。验证：填写实际完成的测试。'
git push origin main
```

新增测试和文档也应一并选中提交。不要照抄示例中的验证文字作为实际测试结果。VS Code 的“源代码管理”可以完成查看差异、暂存、填写说明、提交与推送；终端命令与图形界面管理的是同一份 Git 历史。

建议的标题前缀：

| 前缀 | 用途 |
| --- | --- |
| `fix:` | 修复失败、错误状态或回归 |
| `feat:` | 增加可用功能 |
| `docs:` | 文档与研究记录 |
| `test:` | 测试与验收 |
| `chore:` | 依赖、仓库和维护事项 |

提交正文写清“原因、修改、验证”，必要时注明“宿舍/工位”。一次提交围绕一个明确目的；不要把已经完成的多项不相关工作长期攒在未提交状态。

## 5. 两台电脑都改了怎么办

如果 push 被拒绝，先保留本机修改和提交：

```powershell
git status
git fetch origin
git log --oneline --graph --all -15
```

本机修改已提交、没有其他运行状态问题时，可合并远端历史：

```powershell
git merge origin/main
```

Git 能自动合并无冲突的代码。同一位置冲突时，按实际功能保留正确内容，运行相关验证后暂存并提交合并；不确定时让 Work Buddy/Codex 基于双方差异处理。不要用 `push --force` 覆盖另一台电脑的提交，也不要用 `reset --hard` 丢弃尚未保存的修改。

需要中止尚未完成的合并可用 `git merge --abort`；这不是完成同步。Word 等二进制文件不能按代码行自动合并，建议一个时间段只在一台电脑编辑。

## 6. 查看记录与撤销错误改动

```powershell
git log --oneline -20
git show --stat HEAD
git log -- packages/site_memory/repository.py
```

GitHub 仓库的提交记录可以查看同样的说明与文件差异。已共享的错误提交优先用 `git revert <提交号>` 创建一条撤销记录，验证后推送，保留双方历史。执行前仍须检查未提交修改与需要撤销的实际范围。

## 7. 给 Work Buddy / Codex 的日常指令

```text
先检查当前分支和 git status；工作区干净时拉取最新提交。
实现本次要求，完成相关测试并同步文档。
不要提交密钥、实际配置、数据库、会话、依赖目录或采集结果。
修改后说明原因、行为变化、验证结果和局限。
在本次授权范围内提交并推送到 git-xjf/AutoSpider，保留清晰提交说明。
遇到分叉或冲突先保留双方工作，不强推、不硬重置。
```

只希望讨论方案时明确说明“只分析，不修改/提交”。需要试验较大的改动时可以创建功能分支，再合并回 main；这不是当前个人双机工作必须额外执行的审批步骤。

## 8. 单独管理运行数据

GitHub 同步源代码，不自动同步 Site Memory、账号和对话。若以后要共享已验证记忆，优先设计脱敏的规则导出/导入，不让两台电脑直接同步写入同一个 SQLite 活文件。

历史数据备份在停止相关进程后进行，或使用数据库一致性备份。密钥通过每台电脑自己的环境设置提供，不粘贴进提交说明、Issue 或助手反馈。私有仓库仍不应保存真实凭据。
