# 素材审核与发布排期 Agent

审核合成素材、识别时段冲突并保存排期草稿。原硬要求被占用时，只保存待确认建议；用户显式确认且重新校验通过后才设置有效时段。**所有结果均未发布，项目没有发布接口。**

当前已实现 CLI、LangChain 工具接入、LangGraph 决策循环、SQLite 幂等事务、确认与失败处理，并生成合成数据和运行记录。已使用百炼 **qwen3.8-max** 通过正常/冲突两条真实决策路径及六项业务验收；自动化结果为 **64 项离线通过 + 2 项真实模型通过**。历史测试见 [Qwen 测试报告](docs/06-qwen-evaluation.md)，最终结果以 [提交审查报告](docs/07-release-audit.md) 为准。

## 快速运行

以下命令适用于 macOS/Linux，需要 Python 3.11 及以上：

```bash
git clone https://github.com/Hunny-coder/material-review-scheduler-agent.git
cd material-review-scheduler-agent
# 中文目录下构建时使用本机支持的 UTF-8 locale（macOS 可用 en_US.UTF-8）。
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
python -m material_scheduler.cli --version
python -m material_scheduler.cli run --mode mock \
  --input examples/inputs/normal.json --db var/demo.sqlite3
```

已有本地环境可从 `source .venv/bin/activate` 开始。真实模型配置不随仓库分发，离线模式无需密钥。

已在两个独立 Python 3.11 环境安装同一锁文件并验证。首次安装需要网络或预下载的 wheel；`--mode mock` 的业务运行不需要网络或密钥。Windows 可在 WSL 执行上述步骤；未验证原生 Windows 安装。

实际验证版本：Python **3.11.15**、LangChain **1.4.7**、LangGraph **1.2.18**、langchain-openai **1.7.3**、Pydantic **2.14.1**、SQLite **3.50.4**。完整精确依赖见 [requirements.lock.txt](requirements.lock.txt)。

## 冲突与确认

合成排期中，2026-11-01 的 18:00—19:00 已占用，19:00—19:30 可用。

```bash
python -m material_scheduler.cli run --mode mock \
  --input examples/inputs/conflict.json --db var/conflict.sqlite3
```

预期输出：`suggested_slot=19:00`、`confirmation_status=pending`、`effective_slot=null`、`draft_status=pending_confirmation`。此命令退出码 **2**，表示等待用户确认，不是运行异常。

保存输出中的真实草稿 ID、提案版本与 token。token 仅在首次保存时返回；重复提交只返回原回执，不能取回明文 token。程序在数据库中只存 token 摘要，Trace 中将其脱敏；公开归档也统一脱敏 token，不能用归档中的 [redacted] 进行确认。

```bash
read -r DRAFT_ID
read -r CONFIRMATION_TOKEN
python -m material_scheduler.cli confirm --mode mock \
  --draft-id "$DRAFT_ID" --proposal-version 1 \
  --token "$CONFIRMATION_TOKEN" --decision approve \
  --db var/conflict.sqlite3
```

请使用实际输出的版本。`--decision reject` 表示拒绝。确认时素材、规则或排期变化会使旧提案失效，返回错误与重新执行原请求的指引；本期不在确认命令中自动生成新提案。确认成功仍只是草稿。

## 数据、测试与运行证据

```bash
# 幂等生成合成数据；已修改的数据不会被覆盖。
python scripts/generate_data.py

# 日常离线回归，不调用真实模型。
python -m pytest -q -m "not live"

# 执行完整离线场景，输出目录必须是新目录。
python scripts/run_examples.py --mode mock --output var/reproduced-runs

# 保存失败：退出码 4，草稿和回执事务回滚。
python -m material_scheduler.cli run --mode mock \
  --input examples/inputs/failure.json \
  --fault-config examples/fixtures/save-failure.json \
  --db var/failure.sqlite3

# 调用次数上限：退出码 6，不执行超额调用。
python -m material_scheduler.cli run --mode mock \
  --input examples/inputs/budget.json \
  --fault-config examples/fixtures/budget.json --max-tool-calls 2 \
  --db var/budget.sqlite3
```

默认运行记录写入 `var/runs/<时间戳>/`。`--run-dir` 可指定新目录；拒绝覆盖已有 Trace。重复提交测试应复用同一数据库；不同场景建议使用独立数据库。

已提交的执行证据索引见 [examples/runs/mock-index.json](examples/runs/mock-index.json)。每组包含实际输入、输出、JSONL Trace、环境元数据和断言结果。六项必验及排期变更复跑的对应关系见 [评估文档](docs/03-evaluation.md)。

Mock 的动作由观察驱动的脚本产生，通过真实业务工具和 LangGraph 执行；它验证流程与业务边界，**不证明真实模型决策效果，也不能代替题目要求的真实 ReAct 证据**。

## 接入真实模型

```bash
test -f .env || cp .env.example .env
# 本机编辑 .env，填写 MODEL_NAME 和 MODEL_API_KEY。
# 百炼设置 MODEL_PROVIDER=bailian，由程序自动选择官方接口。
# 百炼 Qwen 使用 MODEL_NAME=qwen3.8-max、MODEL_ENABLE_THINKING=false。
python -m material_scheduler.cli run --mode live \
  --input examples/inputs/conflict.json --db var/live.sqlite3

# 实际验证两条真实模型路径；生成新目录的完整记录。
python scripts/run_examples.py --mode live --output var/live-runs
python scripts/run_live_acceptance.py --output var/live-acceptance-runs
python -m pytest -q -m live
```

当前支持 MODEL_PROVIDER=bailian 或 openai；具体模型由配置决定。接口随 provider 自动选择，.env 与 .env.example 不包含 MODEL_BASE_URL。`MODEL_API_KEY` 显式传给 SDK；不要把密钥提交到仓库或对话。程序只加载当前项目 `.env`，不向父目录搜索。真实模式失败会明确返回 `MODEL_UNAVAILABLE`，不会静默降级到 Mock。默认不启用远程 Trace。

本机已配置用户授权的百炼密钥，`.env` 权限为 0600 且被 Git 忽略。`MODEL_ENABLE_THINKING` 可留空表示不传参数，或显式设 true/false。真实测试中发现空工具结束标记时，程序拒绝该动作、计入预算并向模型反馈；不会因此虚报保存成功。

真实调用时，模型选择工具 → 工具执行 → `ToolMessage` 返回观察 → 模型再次选择动作。每次保存还受独立的规则、确认、时段和版本检查约束。每项默认最多 12 次模型调用、12 次工具调用；批量总预算各 60 次，图另有 200 步兜底。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 本次草稿处理完成，未发布 |
| 2 | 等待资料或确认，或授权范围内没有空闲槽 |
| 3 | 审核规则阻断 |
| 4 | 工具、数据库或保存确定失败 |
| 5 | 输入不合法或幂等键冲突 |
| 6 | 调用预算耗尽、保存结果未知或其他未完成状态 |
| 7 | 真实模型不可用 |

## 评审入口与范围

- [需求与成功标准](docs/01-requirements.md)
- [方案、框架职责与取舍](docs/02-solution.md)
- [实际评估结果与证据](docs/03-evaluation.md)
- [AI 使用与人工修改记录](docs/04-ai-usage.md)
- [复盘、实际用时与未完成项](docs/05-retrospective.md)
- [Qwen3.8-Max 真实模型测试](docs/06-qwen-evaluation.md)
- [最终提交审查与功能清单](docs/07-release-audit.md)
- [题号、仓库与提交方式](SUBMISSION.md)
- [原开发设计文档](项目开发文档.md)

只处理合成元数据和演示规则，不提供通用多模态审核。单机 CLI、30 分钟槽、最多 5 项输入；只接受带时区的绝对日期，不解析自然语言相对时间。待确认建议不预留时段；没有外部排期系统的并发一致性保证。SQLite 锁等待上限 5 秒；未实现可安全中断任意本地工具的统一 5 秒硬超时。真实模型调用超时为 30 秒。

题号：暂无（用户已确认）。公开仓库：[Hunny-coder/material-review-scheduler-agent](https://github.com/Hunny-coder/material-review-scheduler-agent)。最终远程 commit SHA 以交付消息及 GitHub main HEAD 为准；本地 Git 开发历史和历史运行记录中的 SHA 不等于远程发布快照的 SHA。

更新公开证据后，上传前执行 `python scripts/prepare_public_evidence.py` 和 `python scripts/verify_submission.py`，后者会拒绝明文确认 token 或密钥。
