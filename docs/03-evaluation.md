# 测试预期、实际结果与限制

最新验证结论：核心离线业务链路通过，且已完成百炼 Qwen3.8-Max 的两条真实决策路径和六项验收。新增回归后，56 项离线测试与 2 项真实模型测试均通过；详情和原始文件见 [Qwen 测试报告](06-qwen-evaluation.md)。下面保留最初本地开发阶段的离线验证材料，不能将其与后续真实测试混为一谈。

自动化结果和版本摘要保存在 [test-results](../examples/test-results/)。最初第二个独立环境的结果为 **50 passed、2 skipped，2.02 秒，退出码 0**，见 [pytest.txt](../examples/test-results/pytest.txt) 与 [JUnit](../examples/test-results/junit.xml)。当时两个真实测试因缺凭据跳过；现已补齐真实测试并通过，见 qwen-live-pytest.txt。Ruff 与格式检查均通过，早期命令、环境和退出码见 [verification.json](../examples/test-results/verification.json)。

## 六项必验与执行证据

下列文件均由程序实际执行生成，模式为 **mock**，不是手工填写的预期输出。每组目录都包含 `input.json`、`output.json`、`trace.jsonl`、`run-metadata.json` 和 `assertions.json`。

| 场景 | 预期 | 实际离线结果 | 证据 |
| --- | --- | --- | --- |
| 正常排期 | 原时段可用，保存 confirmed 草稿 | completed，退出码 0；未发布 | [输出](../examples/runs/mock-normal/output.json)、[Trace](../examples/runs/mock-normal/trace.jsonl) |
| 信息缺失 | 补查后仍缺授权编号，不保存 | needs_info，退出码 2，草稿数 0 | [输出](../examples/runs/mock-missing/output.json)、[Trace](../examples/runs/mock-missing/trace.jsonl) |
| 规则风险 | 授权过期，阻断保存 | blocked，退出码 3，附规则与字段证据 | [输出](../examples/runs/mock-risk/output.json)、[Trace](../examples/runs/mock-risk/trace.jsonl) |
| 时段冲突 | 18:00 占用，建议 19:00，须确认 | pending、effective_slot=null，退出码 2 | [输出](../examples/runs/mock-conflict/output.json)、[Trace](../examples/runs/mock-conflict/trace.jsonl) |
| 重复提交 | 同请求第二次返回同草稿，一份记录 | already_saved，ID 相同，草稿数 1 | [输出](../examples/runs/mock-duplicate/output.json)、[断言](../examples/runs/mock-duplicate/assertions.json) |
| 保存失败 | 提交前异常，回滚草稿与回执 | failed，退出码 4，草稿数 0 | [输出](../examples/runs/mock-failure/output.json)、[Trace](../examples/runs/mock-failure/trace.jsonl) |

至少三组正常/缺资料/工具失败的实际材料已覆盖；全部离线场景索引见 [mock-index.json](../examples/runs/mock-index.json)。

## 额外边界

| 验证 | 实际结果与证据 |
| --- | --- |
| 确认并更新同一草稿 | [mock-confirm](../examples/runs/mock-confirm/output.json)：confirmed，19:00 生效，未发布 |
| 更换占用时段后复跑 | [修改前](../examples/runs/mock-before-shift/output.json) 19:00 待确认；[修改后](../examples/runs/mock-shifted-conflict/output.json) 原 18:00 已可用，同一草稿更新 |
| 旧确认失效 | [mock-stale-confirm](../examples/runs/mock-stale-confirm/output.json)：19:00 新占用，SLOT_CONFLICT，不设置有效时段 |
| 可恢复资料 | [mock-recoverable](../examples/runs/mock-recoverable/output.json)：补充授权编号后审核通过并保存 |
| 人工审核 | [mock-manual-review](../examples/runs/mock-manual-review/output.json)：needs_review，无排期写入 |
| 无槽 | [mock-no-slot](../examples/runs/mock-no-slot/output.json)：no_slot，请求调整授权范围 |
| 工具上限 | [mock-budget](../examples/runs/mock-budget/output.json)：只执行两次，第三次不执行，incomplete |
| 越权参数 | [mock-boundary](../examples/runs/mock-boundary/output.json)：拒绝 confirmed 等额外参数，零草稿 |
| 批内冲突 | [mock-batch](../examples/runs/mock-batch/output.json)：后项看到前项占用，候选不同 |
| 素材内指令 | [mock-injection](../examples/runs/mock-injection/output.json)：指令作为数据，不提供发布动作 |

自动化测试另覆盖 UTC/半开区间、window/preferred、输入错误、重复输入规范化、请求键冲突、并发重复、错误 token、拒绝、规则/素材变化、已有草稿被新风险作废、提交后回执恢复、结果未知恢复、保存后预算耗尽仍保留成功事实、Trace 脱敏和拒绝覆盖证据。断言直接检查数据库、回执和状态，不以模型文案判定。

## 两条不同路径

- 正常路径：read_material → check_rules → query_slots → propose_draft → save_draft → finish。
- 冲突路径：read_material → check_rules → query_slots（原时段占用）→ query_slots（更大授权窗口，19:00 空闲）→ propose_draft → save_draft（pending）→ finish。

上述路径来自实际 Mock Trace，证明相同业务工具在不同观察下产生不同流程，不能代替真实 ReAct 证据。后续 Qwen 两条真实路径已在 [retest](../examples/runs/qwen3.8-max/retest/live-index.json) 中补齐；初次真实测试暴露的工具解析异常及修复也如实保留，没有把 Mock 记录标为 live。

另实际执行了 [live-unavailable](../examples/runs/live-unavailable/output.json)：显式清空模型名与密钥时返回 `MODEL_UNAVAILABLE`、退出码 7，零工具调用，验证没有静默降级；该失败场景不作为真实模型能力证明。

## 失败、修复与复验

1. 中文路径下 editable 构建遇到 ASCII locale 编码错误：设置 UTF-8 locale 后安装成功；README 记录此环境要求。
2. dotenv 默认向父目录搜索导致启动阻塞：改为只读取当前项目普通 `.env` 文件；后续版本命令和 CLI 已验证。
3. 检查确认时需避免在已持有 SQLite 写锁的连接外再次发起失效写事务：确认守卫复用当前事务，由仓库统一作废。
4. 已确认草稿的回执重放不能降级成待确认，也不能因执行预算耗尽抹去已提交事实：均有自动化断言。

完成格式与静态检查，使用第二个独立环境按精确锁文件安装并复验 CLI 与测试。未验证操作系统矩阵、真实服务超时行为或外部并发排期。

## 未完成与限制

真实模型两条执行证据已补齐。最新公开仓库与远程提交方式见 SUBMISSION.md，正式题号暂无；最终提交审查与复验见 docs/07-release-audit.md。当前无真实发布、多人认证、全模态审核、生产级工具硬超时或外部事务一致性。

早期 Mock 记录引用代码基线 `029c1d7a0f90ca85fa485b1ce5df203b13fb7494`；Qwen 修复重测引用 `caa6608e98bd266b0f7350687e895ef67ff57f7f`，均含对应源码摘要，可在 Git 历史核对。最终本地提交通过 `git rev-parse HEAD` 获取，不把待创建远程仓库说成已完成交付。
