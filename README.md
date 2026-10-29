# 素材审核与发布排期 Agent

题号：**1**  
仓库：https://github.com/Hunny-coder/material-review-scheduler-agent

审核素材、检查时段冲突、生成待确认排期，仅保存草稿。

## 环境

Python ≥3.11 · LangChain 1.4.7 · LangGraph 1.2.18 · 百炼 qwen3.8-max

## 安装与离线运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
python -m material_scheduler.cli run --mode mock --input examples/inputs/normal.json
```

Mock 验证流程，不代表真实模型效果。

## 真实模型

```bash
test -f .env || cp .env.example .env
# 在 .env 填写 MODEL_API_KEY，保留 MODEL_PROVIDER=bailian。
python -m material_scheduler.cli run --mode live --input examples/inputs/conflict.json
```

## 测试

```bash
python -m pytest -q -m "not live"
python -m pytest -q -m live
```

已通过：64 项离线测试、2 项真实模型测试、6 项业务验收。

## 验收入口

- [提交信息](SUBMISSION.md)
- [评估与运行记录](docs/03-evaluation.md)
- [最终功能审查](docs/07-release-audit.md)
- [开发文档](项目开发文档.md)
