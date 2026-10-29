# Qwen3.8-Max 真实模型测试

测试日期：2026-10-29，Asia/Shanghai。用户授权使用百炼服务，本次模型为 **qwen3.8-max**；用户写法中的额外连字符已按官方 API ID 纠正。[官方模型说明](https://www.alibabacloud.com/help/en/model-studio/qwen3-8-max)

本轮观察用时：17:41:17—17:52:15，约 11 分钟，含定位修复与结果归档。

结论：**56 项离线回归通过，2 项真实模型集成测试通过，六项真实模型业务验收全部通过。** 真实模型使用现有 LangChain/LangGraph 循环，业务工具仍为本地合成数据与 SQLite；没有真实发布。

## 配置与版本

- 兼容接口：`https://dashscope.aliyuncs.com/compatible-mode/v1`，北京地域。
- `MODEL_PROVIDER=openai` 使用兼容协议，实际服务与响应模型均为百炼 `qwen3.8-max`。
- `MODEL_ENABLE_THINKING=false`，每次 SDK 调用超时 30 秒、自动重试关闭，业务调用预算保持每项各 12 次。
- 版本沿用锁文件：LangChain 1.4.7、LangGraph 1.2.18、langchain-openai 1.7.3、Python 3.11.15。
- 密钥仅存本机 `.env`，权限 0600，Git 忽略；未进入代码、Trace、测试报告或提交。

[公开配置](../examples/test-results/qwen-public-config.json) 不含密钥。[连通性检查](../examples/test-results/qwen-preflight.json) 返回实际模型名和 OK，仅证明接口可用，不作为 Agent 决策证据。兼容域名仍可使用，官方也提供业务空间专属域名。[百炼兼容文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)

## 测试结果

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 离线回归 | 56 passed，2 deselected，2.30 秒 | [pytest](../examples/test-results/qwen-offline-regression.txt)、[JUnit](../examples/test-results/qwen-offline-junit.xml) |
| 真实模型集成 | 2 passed，56 deselected，37.97 秒；无跳过 | [pytest](../examples/test-results/qwen-live-pytest.txt)、[JUnit](../examples/test-results/qwen-live-junit.xml) |
| 六项业务验收 | 6 / 6 passed | [汇总](../examples/runs/qwen3.8-max/acceptance/summary.json) |
| 静态检查与格式 | Ruff 与 format 检查通过 | [Ruff](../examples/test-results/qwen-ruff.txt)、[format](../examples/test-results/qwen-format.txt) |

最终报告、源码摘要、8 组真实运行目录及密钥排除检查的汇总见 [qwen-validation-summary.json](../examples/test-results/qwen-validation-summary.json)。

| 验收场景 | 实际行为 | 输出 |
| --- | --- | --- |
| 正常排期 | 原时段可用，保存 confirmed 草稿，completed / 0 | [normal](../examples/runs/qwen3.8-max/acceptance/normal/output.json) |
| 信息缺失 | 缺少授权编号，needs_info，零草稿，awaiting_user / 2 | [missing](../examples/runs/qwen3.8-max/acceptance/missing/output.json) |
| 规则风险 | 授权过期，blocked，零草稿，退出码 3 | [risk](../examples/runs/qwen3.8-max/acceptance/risk/output.json) |
| 时段冲突 | 原 18:00 保留，建议 19:00，pending，无有效时段，退出码 2 | [conflict](../examples/runs/qwen3.8-max/acceptance/conflict/output.json) |
| 重复提交 | 与 conflict 复用数据库和请求，already_saved、相同 ID、草稿数 1 | [duplicate](../examples/runs/qwen3.8-max/acceptance/duplicate/output.json) |
| 保存失败 | 真实模型调用本地保存工具，提交前故障回滚，failed / 4，草稿数 0 | [failure](../examples/runs/qwen3.8-max/acceptance/failure/output.json) |

失败场景只对**本地业务工具**注入 `before_commit`；模型始终为真实 Qwen，未切换成 Mock。测试脚本通过应用服务接口注入，常规 CLI 仍禁止在 live 命令中传 `--fault-config`。

每个验收目录含 input、output、trace、run-metadata、基础 assertions 和 acceptance 断言。所有 Trace 的动作来源为 `model`，另外记录实际响应模型、调用序号与 token 数字统计，不记录内部思维内容。

## 模型根据不同观察改变动作

两条完整重测路径保存在 [live-index.json](../examples/runs/qwen3.8-max/retest/live-index.json)：

1. [正常 Trace](../examples/runs/qwen3.8-max/retest/live-normal/trace.jsonl)：读取素材 → 检查规则 → 查询原时段（空闲）→ 登记原时段提案 → 保存 → 结束。
2. [冲突 Trace](../examples/runs/qwen3.8-max/retest/live-conflict/trace.jsonl)：读取素材 → 检查规则 → 查询原时段（占用）→ 模型选择扩大到授权窗口查询 → 观察 19:00 空闲 → 登记替代提案 → 保存待确认草稿 → 结束。

冲突路径没有把 19:00 擅自写为有效排期。模型接收真实工具观察后选择第二次查询，满足“模型选择动作 → 工具执行 → 观察 → 模型调整动作”的循环要求。

## 真实测试发现的问题与修复

首次正常场景在登记提案后出现：供应商返回 `finish_reason=tool_calls`，但 SDK 没有解析出可执行调用。旧图将其当作模型结束，因此返回 `incomplete / not_attempted`，没有虚报保存成功。[原失败输出](../examples/runs/qwen3.8-max/live-normal/output.json)、[原 Trace](../examples/runs/qwen3.8-max/live-normal/trace.jsonl) 均保留，原断言为 false，不冒充通过。

随后单独重放最后一次模型上下文时返回了合法 save_draft 调用；这说明存在不稳定表现，尚不能仅凭这些记录判定是模型生成还是兼容层解析的根因。[工具结构诊断](../examples/test-results/qwen-tool-parse-diagnostic.json)

修复后，将这类返回识别为 `INVALID_MODEL_ACTION`，拒绝执行并计入工具预算，把校验失败作为显式运行时观察反馈给模型；下一步仍由模型选择，程序不会自动补一次保存。对非法参数和空工具结束标记分别增加回归测试。

正常重测中实际再次出现一次异常，模型收到反馈后选择合法 save_draft，并成功结束；冲突重测不需要该修复路径。固定分支只是校验边界，真正的下一工具和参数由 Qwen 再次输出。

业务源码及修复基线为 `caa6608e98bd266b0f7350687e895ef67ff57f7f`，运行元数据含源码摘要。此前失败记录对应 `547caa6` 阶段。后续交付提交补充测试脚本、报告与文档，不改写历史失败。

## 复现命令

本机 `.env` 已配置；新机器需自行配置相同服务，密钥不随仓库分发。输出目录必须为新目录：

```bash
source .venv/bin/activate
python scripts/run_examples.py --mode live --output var/qwen-two-paths
python scripts/run_live_acceptance.py --output var/qwen-six-cases
python -m pytest -q -m live
python -m pytest -q -m "not live"
```

这些命令会产生真实模型 API 用量。六项验收脚本在失败时返回非零退出码并保留逐项结果，不静默切换模型或 Mock。

本次是小样本功能验收，不证明长期稳定性、所有提示下的安全性或多模型对比优势。统一工具硬超时、外部排期并发一致性、生产部署、题号为 1，公开交付方式与最终审查见 SUBMISSION.md 和 docs/07-release-audit.md。
