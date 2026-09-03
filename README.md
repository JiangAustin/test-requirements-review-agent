# Requirements Review Agent

这是一个面向需求评审的本地仓库入口：通过 VS Code Copilot custom Agent 加 Python MCP service，对文本型 PDF 和含表格 PDF 做 requirements review，输出 JSON、Markdown、DOCX 三种一致产物。它聚焦 manual 与 automation test gaps、需求可测试性得分、建议场景覆盖度，不做真实测试覆盖率计算。

## 项目目标与限制

- project purpose：把文本型 PDF requirements 拆成可追溯的原子需求，识别 manual/automation 缺口，生成本地报告。
- limits：当前仅支持 text-table PDF only，no OCR，不支持 scanned PDF、图片 OCR、远程文件存储，也不计算现有 test-case coverage 或测试执行覆盖率。
- metrics disclaimer：需求可测试性得分不是现有用例覆盖率，也不是测试执行覆盖率。建议场景覆盖度不是现有用例覆盖率，也不是测试执行覆盖率。

## 一键接入任意项目

前置条件：安装 [uv](https://docs.astral.sh/uv/)、VS Code，并具备 GitHub Copilot Chat 权限。Copilot Business 或 Enterprise 环境还需要管理员允许 MCP admin enablement。

在需要评审需求的目标项目根目录运行一条命令：

```powershell
uvx --from git+https://github.com/JiangAustin/test-requirements-review-agent.git rra init
```

该命令会幂等创建或合并：

- `.github/agents/requirements-review.agent.md`
- `.vscode/mcp.json`
- `.gitignore` 中的 `.runs/`、`.env`、`inputs/`

它不会覆盖已有的不同 Agent 文件或同名 MCP server 配置；发生冲突时会停止并说明路径。默认 `home-iot-v1` rule pack 已内置，不需要复制 `rules/` 目录。

初始化后在 VS Code 执行 Reload Window，打开 Copilot Chat 并选择 **Requirements Review**。首次启动 MCP 时需要网络访问 GitHub，并在 VS Code 中确认 workspace trust 与 MCP start/trust。

可选健康检查：

```powershell
uvx --from git+https://github.com/JiangAustin/test-requirements-review-agent.git rra doctor
```

`doctor` 检查 `uvx`、Agent、MCP 配置和内置 rule pack；全部正常时退出码为 0。

## Rule pack 覆盖

默认直接使用内置 `home-iot-v1`。如需项目定制，在目标项目创建 `rules/<name>.yaml`，评审时传入 `<name>`；项目文件优先于同名内置 rule pack。PDF 仍必须位于当前 workspace 内，运行产物写入当前项目的 `.runs/`。

## 仓库开发

开发本项目需要 Python >=3.12,<3.14。在 repository root 执行：

```powershell
uv sync --dev
uv run python -m pytest
uv run ruff check .
uv run mypy src
uv run mcp dev src/requirements_review_agent/server.py
```

## 打开仓库与发现 Agent

- 开发时必须打开 repository root，而不是只打开子目录，否则 custom Agent discovery 和 .vscode/mcp.json 可能失效。
- Agent 文件位于 .github/agents/requirements-review.agent.md，名称是 Requirements Review。
- 在 Copilot Chat 中选择 Agent 时，确认看到 Requirements Review。
- 如需检查 Chat customization diagnostics，可打开 Problems panel，查看 .github/agents/requirements-review.agent.md 的 frontmatter 或正文告警。

## MCP 启动与信任

- `rra init` 生成的 MCP 配置通过 `uvx` 从 GitHub 获取并缓存 service，不要求目标项目创建 Python virtual environment。
- 本仓库自己的开发配置位于 .vscode/mcp.json，使用 `uv run requirements-review-mcp`。
- 首次加载时在 VS Code 中信任 workspace，并允许 MCP start/trust。
- 可通过 MCP: List Servers 检查 requirements-review 是否已注册并启动。
- 用于开发调试的命令是 uv run mcp dev src/requirements_review_agent/server.py。

## 六工具工作流与示例

Requirements Review Agent 只应驱动这六个工具：prepare_review、get_analysis_batch、submit_analysis、run_provider_analysis、finalize_review、get_review_status。

demo usage：

1. 提供一个 workspace-local PDF path。
2. 选择 rule pack，例如 home-iot-v1。
3. 选择 provider mode：copilot、company_api 或 local。
4. 确认 provider 和 data destination。
5. 执行 prepare_review。
6. Copilot 模式逐批调用 get_analysis_batch 和 submit_analysis；company_api/local 模式调用 run_provider_analysis。
7. 执行 finalize_review 与 get_review_status，并从 `.runs/<run-id>/reports/` 打开 JSON、Markdown、DOCX。

## Provider 模式、环境变量与安全

- copilot：数据发送到 GitHub Copilot model selected in VS Code。
- company_api：数据发送到你显式配置的 company API；继续前必须做显式确认，明确 external transfer 和 data destination。
- local：数据发送到本地推理服务；继续前仍需说明 data destination，local 模式的数据不离开本机。
- 只使用这五个变量：RRA_COMPANY_BASE_URL、RRA_COMPANY_API_KEY、RRA_COMPANY_MODEL、RRA_LOCAL_BASE_URL、RRA_LOCAL_MODEL。
- .env.example 只给变量名；真实 secrets never 写入 config、log、report、README、mcp.json 或 agent frontmatter。
- 服务只读取当前进程的环境变量，不会自动加载 .env。`.env` 保持 ignored，但使用前必须由可信工具加载，或在启动 VS Code/MCP 的同一 PowerShell 会话中显式设置变量。

company_api 模式可在当前 PowerShell 会话中设置：

```powershell
$env:RRA_COMPANY_BASE_URL = Read-Host "Company API base URL"
$env:RRA_COMPANY_API_KEY = Read-Host "Company API key"
$env:RRA_COMPANY_MODEL = Read-Host "Company model"
```

local 模式不使用 API key：

```powershell
$env:RRA_LOCAL_BASE_URL = Read-Host "Local API base URL"
$env:RRA_LOCAL_MODEL = Read-Host "Local model"
```

## 本地产物与忽略规则

- .runs/ 用于保存本地运行中间结果和最终报告。
- .env 用于本地环境变量，保持 ignored。
- input PDFs 必须保持 local+ignored，避免把真实需求文档加入版本库。
- secrets never 出现在 committed config、日志或报告。

## 输出格式与指标解释

- JSON 是唯一事实源。
- Markdown 与 DOCX 由同一 JSON 派生，需求 ID、分数、gap question、evidence quote 必须一致。
- 需求可测试性得分表示适用检查项中已明确项的加权结果，不是真实测试覆盖率。
- 建议场景覆盖度表示建议测试设计对适用 scenario categories 的覆盖程度，不表示现有用例覆盖率，也不表示测试执行覆盖率。

## 稳定错误码

- PDF_ENCRYPTED：PDF 已加密，当前流程会停止。
- PDF_DAMAGED：PDF 损坏或无法可靠解析。
- PDF_SCANNED：检测到 scanned/image-only PDF，当前版本 no OCR。
- PDF_OUTSIDE_WORKSPACE：输入文件不在当前 workspace 内。
- RULE_PACK_INVALID：rule pack 不存在、路径非法或结构错误。
- ANALYSIS_INVALID：分析结果不符合 schema 或证据约束。
- PROVIDER_UNAVAILABLE：所选 provider 不可用。
- REPORT_PARTIAL：DOCX 失败但 JSON/Markdown 已保留。

## 无 OCR 的处理方式

- 当前版本 no OCR。
- 对 scanned/image-only PDF 的 remediation 是先转成可复制文本的 PDF，或由需求方提供文本型原件，再重新运行评审。

## Synthetic demo 与测试

- synthetic demo 会构造本地 text+table PDF，用于离线验证 prepare_review -> get_analysis_batch -> submit_analysis -> finalize_review -> get_review_status。
- 运行 E2E：uv run pytest tests/test_e2e.py -v
- 运行全量测试：uv run pytest -v
- 锁文件检查：py -3.13 -m uv lock --check

## 自定义 Agent 检查点

- 如果 Agent 未显示，先确认打开的是 repository root。
- 再检查 Problems panel 中 .github/agents/requirements-review.agent.md 是否有 YAML frontmatter 诊断。
- 再确认 MCP server 已被信任并成功启动。
