# 合成输入与实际执行记录

`inputs/` 有 12 组无隐私的合成输入，`fixtures/` 有排期变体和显式故障配置，均由 `scripts/generate_data.py` 生成。默认 18:00—19:00 被占用，19:00 可用；normal/failure 使用次日空闲时段。

`runs/mock-index.json` 索引 18 组实际离线执行。每组至少包含输入、实际输出、JSONL Trace、环境/数据/代码元数据与断言。正常、信息不足、工具失败三类及其余必验场景均已记录。记录中的绝对路径是生成时的本地路径；移动仓库后按本文件相对目录打开记录，并重新运行即可生成当前路径。

normal 和 conflict 使用同一模型脚本和业务工具：前者查询一次后保存，后者观察原时段占用后扩大查询，再保存待确认建议。所有 Mock 动作明确标为 `mock_script`，不能作为真实模型决策效果证据。

duplicate 与 conflict 复用同一数据库，草稿计数为 1。before-shift 与 shifted-conflict 在同一数据库、同一请求下更换排期数据；before-stale 与 stale-confirm 则在用户确认前增加候选占用。各组输出与断言保留真实草稿 ID，未手工改成演示 ID。

确认 token 在 Trace 与确认输入中脱敏。提交版历史输出中的确认 token 也统一脱敏，不公开合成数据库的凭据；本地重新运行后从 CLI 实际输出取得新 token。测试数据授权编号均以 SYNTH 开头。

重新生成执行材料时使用一个新目录，避免覆盖旧证据：

```bash
python scripts/run_examples.py --mode mock --output var/new-evidence
```

`runs/live-unavailable/` 是真实模式未配置模型时的失败记录，退出码 7，不会降级。它不是成功的真实模型执行路径。配置本机 `.env` 后可用 `--mode live` 生成 normal/conflict 两条真实运行记录。

`test-results/` 保存第二个独立环境的 pytest、JUnit、静态检查、版本、执行命令和退出码。业务代码基线为 `029c1d7a0f90ca85fa485b1ce5df203b13fb7494`，元数据还记录源码摘要供核对。

后续真实模型材料在 `runs/qwen3.8-max/`：首次失败保留在 live-normal，修复后的两条成功路径位于 retest，六项业务验收位于 acceptance。修复后代码基线为 `caa6608e98bd266b0f7350687e895ef67ff57f7f`。最新结果为 56 项离线回归、2 项真实模型集成和 6 项真实验收全部通过，详情见 docs/06-qwen-evaluation.md。历史 Mock 记录仍按原版本保留，不能据新源码要求它们的摘要相同。

最新发布证据位于 runs/release，包含六项真实验收与正常/冲突两条路径，源码基线 a3b2ec7。此轮执行前已删除环境文件中的 MODEL_BASE_URL。最终自动化为 64 项离线、2 项真实模型通过；历史文件只做路径与主机名脱敏，不改变业务事实。
