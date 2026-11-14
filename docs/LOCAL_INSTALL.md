# 当前机器安装说明

本工作区使用两个 Python 运行时：

- AutoSpider MCP 与 HTTP/JSON/Playwright 执行器：`D:\conda_envs\mytorch\python.exe`（Python 3.11）
- DeerFlow 2.0 后端：`deer-flow\backend\.venv`（Python 3.12，由 `uv` 按上游锁文件管理）

DeerFlow 2.0 的 `requires-python` 是 `>=3.12`，因此不要把 DeerFlow 后端强装进
`mytorch`，也不要升级 `mytorch` 的 Python 主版本。

## 模型切换

密钥只放在 `deer-flow\.env`，`config.yaml` 只保存 `$环境变量名`。默认已经选择
`glm-5.3`。以后切换模型：

```powershell
# DeepSeek：先在 deer-flow\.env 中填写 DEEPSEEK_API_KEY
D:\conda_envs\mytorch\python.exe scripts\configure_llm.py deepseek --model deepseek-chat

# 通义千问：先在 deer-flow\.env 中填写 DASHSCOPE_API_KEY
D:\conda_envs\mytorch\python.exe scripts\configure_llm.py qwen --model qwen-plus

# 切回智谱
D:\conda_envs\mytorch\python.exe scripts\configure_llm.py glm --model glm-5.3
```

可用 `--api-base` 覆盖兼容接口地址，用 `--thinking` 或 `--no-thinking` 覆盖模型的
推理能力标志。每次切换都会生成 `config.yaml.model.bak`。

GLM-5.3 不支持关闭思考，只接受 `low`、`high`、`max`。切换脚本默认在两个运行分支都发送 `thinking.type=enabled` 和 `reasoning_effort=low`，避免爬虫、子智能体、摘要或记忆更新发送 `disabled` 而报错。`--no-thinking` 仅控制 DeerFlow 的模式开关，无法关闭 GLM-5.3 的实际思考。

若需要提高普通对话的推理强度，可执行 `scripts\configure_llm.py glm --model glm-5.3 --reasoning-effort high`（也支持 `max`）；爬虫和后台系统任务的低消耗分支仍使用 `low`。暂不启用 DeerFlow 通用推理档位选择器，因为它提供的 `medium` 不是 GLM-5.3 的合法档位。模型配置调整后重启 Gateway，以刷新已缓存的记忆模型实例。

## 启动顺序

1. 确认 `deer-flow/extensions_config.json` 中只有 `autospider` MCP，且 `AUTOSPIDER_ENGINE=native`。该值启用自动分流的 HTTP/JSON/Playwright 混合执行器；不需要启动 EasySpider，也不需要监听 8074。
2. 在 `deer-flow` 中启动 Gateway 和前端；Windows 本机没有 nginx 时可分别启动，
   Gateway 使用 8001 端口，前端使用 3000 端口。
3. DeerFlow 的 AutoSpider MCP 子进程会自动调用 `mytorch`。

本机可直接使用以下脚本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_deerflow_windows.ps1

# 如果 3000 已被其他程序占用
powershell -ExecutionPolicy Bypass -File scripts\start_deerflow_windows.ps1 -FrontendPort 3001

# 停止 DeerFlow 后台进程
powershell -ExecutionPolicy Bypass -File scripts\stop_deerflow_windows.ps1
```

由于本机没有 Docker、WSL 和 nginx，DeerFlow 采用原生 Windows 分端口模式，访问
`http://localhost:3000`；统一入口 `http://localhost:2026` 不启用。
