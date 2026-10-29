# 调用流程、动态决策与技术取舍

CLI → Pydantic 校验 → 请求指纹注册 → 按素材建立 Runtime → LangGraph 模型/工具循环 → 确定性组装结果 → 写入本地证据。

实际版本为 LangChain 1.4.7、LangGraph 1.2.18、langchain-openai 1.7.3、Python 3.11.15。版本锁见根目录 `requirements.lock.txt`，每组记录也保存环境版本。

LangChain 提供 `init_chat_model`、`bind_tools`、消息与 `@tool` 封装；LangGraph 的 `StateGraph` 编排循环和条件边。未重复实现框架本身，未使用旧的 `create_react_agent`。模型收到工具 JSON Schema，其中含可公开的 `decision_summary`；工具观察以 `ToolMessage` 反馈。[官方模型接口](https://docs.langchain.com/oss/python/langchain/models)、[官方循环示例](https://docs.langchain.com/oss/python/langgraph/workflows-agents)

## 模型决策与确定性边界

真实模式由模型选择工具及参数；无冲突可直接登记原时段，冲突时可扩大查询、建议替代、请求确认或停止。图只提供执行骨架。`finish` 表示结束此次执行，最终状态仍由已获取证据确定。没有保存回执时，即使模型声称“成功”，也不会生成成功结果。

Mock 是单独的、明确标识的观察驱动脚本，仅模拟这些工具路径。离线 Trace 和百炼 Qwen3.8-Max 的实际真实模型 Trace 均已保存；两条真实决策路径及六项验收结果见 [模型报告](06-qwen-evaluation.md)。

| 工具 | 职责 |
| --- | --- |
| read_material / read_supplement | 读取合成资料，补查只填空，不覆盖已有字段 |
| check_rules | 必填项、合成授权状态、演示禁用词、标题长度和渠道匹配 |
| query_slots | 授权范围内、同渠道账号、半开区间冲突查询，返回最多三个候选 |
| propose_draft | 候选必须来自有效查询；代码决定是否需要确认，生成提案版本 |
| save_draft | 服务端注入请求、幂等键和原硬约束；事务保存并返回可信回执 |

模型无法向工具传入 `confirmed=true`、任意有效时段或其他素材 ID。额外参数会被拒绝。保存前再次核对素材、规则及排期指纹。重跑时发现证据变化，会使旧草稿失效并使用新提案版本。

## 幂等与保存事实

请求唯一键为 `(request_id, request_revision)`，绑定输入指纹；草稿唯一键再加素材、渠道与账号；保存操作键加入提案版本。相同操作重放返回原草稿 ID。SQLite `BEGIN IMMEDIATE` 串行化冲突检查、草稿写入和回执写入；任一步在提交前失败均回滚。

未确认的替代建议保存为 `pending_confirmation`，不写入有效时段，也不占用槽。确认命令以本机操作者显式输入的 token 为依据，在事务中重验并更新同一草稿。旧证据变化时作废并指引用户重跑；确认命令本身不调用模型自动改选时间。

提交后无法核验回执时返回 `unknown / incomplete`；使用相同请求恢复查询，可得到已提交回执。所有成功文案都是草稿保存成功。

## 预算、错误和审计

默认每项 12 次模型调用、12 次工具调用，最多五项且全批各 60 次。失败、非法参数、拒绝动作和 `finish` 都计入调用次数。图 `recursion_limit=200` 是额外兜底。超额动作不执行，明确返回 `incomplete`，已保存的事实仍保留。

真实 SDK 调用超时 30 秒、关闭 SDK 自动重试。SQLite 锁等待 5 秒；业务工具使用本地普通文件，不承诺可中断任意工具的统一硬超时。数据库仅对单机草稿写入提供串行校验；外部 JSON 排期只支持顺序修改后复跑。

Trace 记录动作来源、工具参数、真实观察及可公开依据，关联工具调用 ID。Mock 标记 `mock_script`，真实模式标记 `model`；不生成内部思维过程，不事后伪造决策。确认 token 在 Trace 中脱敏，数据库只存摘要。

## 与原设计的明确差异

- 实际测试文件按 workflows、persistence、boundaries、live_paths 聚合，未为每个业务模块建立独立测试文件。
- 仅安装 OpenAI/兼容接口适配器，不宣称支持所有供应商。
- CLI 使用带时区的绝对日期，不解析自然语言相对日期。
- 确认失效时提示重新执行原请求，不在确认入口内自动重新规划。
- 未实现自然过期 TTL、统一可抢占工具超时、token 找回与多人身份认证。
- 本机已配置用户授权的百炼凭据并完成真实测试；公开交付入口和最终提交方式见 SUBMISSION.md。

后续真实测试增加 `MODEL_ENABLE_THINKING` 显式开关和 `model_response` 数字用量记录。工具结束标记没有可执行动作时，拒绝并计入预算，将错误反馈模型后由其重新选择；原失败记录和回归测试均保留。

提交版改用 MODEL_PROVIDER=bailian 自动选择百炼端点，.env 与 .env.example 已移除 MODEL_BASE_URL。批量候选互斥和失效草稿回显的最终审查修复见 docs/07-release-audit.md。
