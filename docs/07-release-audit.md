# 提交前审查与验收结论

审查基准：根目录 `项目开发文档.md`、题目要求和用户本轮要求。题号为“暂无”，用户已确认；题目为“素材审核与发布排期 Agent”。

结论：**题目要求的 MVP 功能与交付材料已达到验收提交范围。不是设计稿中所有增强项或生产能力都已实现**，剩余限制见文末。本次按用户要求，先从 `.env` 与 `.env.example` 删除 `MODEL_BASE_URL`，再执行全部发布验收。

## 最终测试结果

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 离线回归 | 64 passed，2 deselected，3.38 秒 | [pytest](../examples/test-results/release-offline.txt)、[JUnit](../examples/test-results/release-offline-junit.xml) |
| 真实模型集成 | 2 passed，64 deselected，37.15 秒，无跳过 | [pytest](../examples/test-results/release-live.txt)、[JUnit](../examples/test-results/release-live-junit.xml) |
| 干净源码＋独立依赖环境 | 64 passed，2 deselected，3.59 秒 | [pytest](../examples/test-results/release-clean.txt)、[JUnit](../examples/test-results/release-clean-junit.xml) |
| 六项真实模型业务验收 | 6 / 6 passed | [summary](../examples/runs/release/acceptance/summary.json) |
| 正常与冲突两条真实路径 | completed / awaiting_user，保存事实与确认状态正确 | [索引](../examples/runs/release/two-paths/live-index.json) |

最终业务源码的本地基线为 `a3b2ec762cbcd4890c7d7fa4fbb75951593f6fd5`；8 组发布运行记录都有对应的源码摘要。GitHub 通过发布快照上传，远程提交 SHA 与本地开发历史 SHA 不同；以公开 main HEAD 和交付消息的完整 SHA 为准，源码完整性可用 [submission-audit.json](../examples/test-results/submission-audit.json) 的文件 SHA-256 清单核对。

## 逐项功能与业务核对

| 开发文档 / 验收项 | 核对结果 | 实现或证据 |
| --- | --- | --- |
| 用户目标与范围 | 完成；只保存草稿，无发布工具 | CLI、工具白名单，固定 not_published |
| 输入素材、元数据、发布时间、规则和已有排期 | 完成；合成资料、输入校验、时间和渠道作用域 | schemas.py、data/、generate_data.py |
| 每项审核、风险、建议、待确认、保存结果 | 完成；业务结果字段由工具事实组装 | tools.py / Runtime.result |
| 正常排期 E01 | 通过 | [normal](../examples/runs/release/acceptance/normal/output.json) |
| 信息缺失 E02 | 通过；补查或返回 needs_info，无草稿 | [missing](../examples/runs/release/acceptance/missing/output.json) |
| 规则风险 E03 | 通过；返回风险规则与证据，阻断保存 | [risk](../examples/runs/release/acceptance/risk/output.json) |
| 时段冲突 E04 | 通过；18:00 原要求保留，19:00 待确认且未生效 | [conflict](../examples/runs/release/acceptance/conflict/output.json) |
| 重复提交 E05 | 通过；同 ID、already_saved、草稿计数 1 | [duplicate](../examples/runs/release/acceptance/duplicate/acceptance.json) |
| 保存失败 E06 | 通过；事务回滚、failed、退出码 4 | [failure](../examples/runs/release/acceptance/failure/output.json) |
| 确认 / 拒绝 / 旧确认失效 E07、E09 | 通过；确认重验且更新同一草稿 | tests/test_persistence.py，发布离线报告 |
| 更换占用时段 E08 | 通过；重新评估，更新同一逻辑草稿 | tests/test_workflows.py / test_changed_occupancy_updates_one_draft |
| 无槽 E10、预算 E11、幂等键冲突 E12 | 通过；明确状态，无越权调整 | tests/test_workflows.py、test_boundaries.py、test_persistence.py |
| 批量冲突 E13 | 通过；已确认草稿参与占用，待确认候选尽量错开，无法错开标互斥 | service.py、tests/test_release_audit.py |
| 越权动作 E14、并发重复 E15、模型不可用 E16 | 通过；拒绝动作、唯一事务、无静默降级 | 发布离线报告 |
| 真实 ReAct 与两条不同路径 | 通过；Qwen 实际选择工具并根据观察改动作 | [正常 Trace](../examples/runs/release/two-paths/live-normal/trace.jsonl)、[冲突 Trace](../examples/runs/release/two-paths/live-conflict/trace.jsonl) |
| LangChain ≥1.0 ＋ LangGraph | 完成；1.4.7 / 1.2.18，功能职责已说明 | requirements.lock.txt、docs/02-solution.md |
| CLI、离线入口、执行上限 | 完成；README 可复制命令，Mock 明确边界 | README、tests/test_boundaries.py |
| 至少三组输入、实际输出、Trace | 完成；六项验收各有完整材料，另有历史离线记录 | examples/runs/ |
| GitHub 目录与五份必需文档 | 完成；源码、测试、资料、依赖和文档在同一仓库 | 根目录与 docs/01—05 |

## 本轮审查复现并修复的问题

新增审查用例首先实际得到 3 failed、1 passed，见 [修复前报告](../examples/test-results/release-before-fix.txt)。原失败没有删除或改成通过。

1. 已经保存后，模型补查到新风险，旧保存回执可能使结果仍回显 completed / 有效时段。现在结束前核对持久化草稿状态及证据；风险优先，失效草稿不再返回有效时段或可用确认 token。历史事务确实已提交的事实仍保留，草稿状态明确为 invalidated。
2. 两项待确认建议可能重叠。现在同批候选优先错开；只剩同一候选时双向返回 mutually_exclusive_with，并明确不能同时确认。
3. 同一模型返回混合合法与非法工具调用时，整批先拒绝，避免不完整的工具协议或部分执行；新观察仍由模型选择如何处理。
4. 百炼连接改为 MODEL_PROVIDER=bailian 自动选择官方端点，不依赖环境文件中的 URL。保留 openai provider 的独立路由，测试确保继承的旧 MODEL_BASE_URL 不会改写目标。

这些修复均有回归用例，包含在最终 64 项离线测试中。

## 清理与公开内容

- 已删除下载缓存、重复验证虚拟环境、临时数据库、临时源码副本、过期草稿运行目录及生成字节码，共清理约 216 MiB。[清理明细](../examples/test-results/cleanup.json)
- 删除未被引用的 empty 排期资料副本，生成脚本同步调整；保留题目要求的合成输入、故障注入和真实运行证据。
- 保留本地 .venv、.runtime 以便直接运行；它们、.env 和 .git 内部文件均不上传。
- .env 权限为 0600，API 密钥不进入代码、提交内容或日志；公开环境模板仅有空密钥。
- 历史证据对本地工作区路径前缀、JUnit 主机名及确认 token 进行自动脱敏；业务状态、时间、草稿 ID、模型动作和非凭据工具结果不变。最新运行记录在写入时就将工作区路径转为相对路径。

## 边界和未完成项

这些不属于题目必需的生产能力：自然语言相对日期解析、任意工具可抢占的统一 5 秒硬超时、token 找回、多人身份认证、多模态审核、生产部署、外部排期系统并发事务与长期模型稳定性评估。本版只接受带时区的绝对日期，SQLite 锁等待 5 秒、模型调用超时 30 秒；确认失效后提示用户重新执行原请求。

因此本结论是“满足题目 MVP 的可验收提交版本”，不是“全部增强设计零限制”。正式数字题号仍为暂无，按用户确认如实提交，不伪造题号。

公开上传预审曾因归档输出含明文确认 token 拒绝一个批次。已使用 scripts/prepare_public_evidence.py 统一脱敏，并在 verify_submission.py 增加不可含明文确认 token 的门禁；未通过审核时没有更新远程 main。
