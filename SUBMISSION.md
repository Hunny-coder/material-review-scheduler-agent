# 验收提交信息

| 项目 | 内容 |
| --- | --- |
| 题号 | 暂无（用户确认，未提供数字编号） |
| 题目 | 素材审核与发布排期 Agent |
| GitHub URL | https://github.com/Hunny-coder/material-review-scheduler-agent |
| 可见性 | 公开，评审可直接读取，无需私有仓库授权 |
| 最终 commit SHA | 以交付消息中的完整 SHA 和该仓库 main HEAD 为准 |
| 运行入口 | README.md，CLI |
| 验收结论与范围 | docs/07-release-audit.md |
| 实际用时与未完成项 | docs/05-retrospective.md |
| 模型 | 百炼 qwen3.8-max，真实工具调用；离线 Mock 单独标注 |
| 最终测试 | 64 项离线 + 2 项真实模型通过；六项真实业务验收 6/6 |

获取所检出版本的最终提交 SHA：

```bash
git rev-parse HEAD
```

远程仓库发布的是已核对的文件快照；本地开发历史中的旧 SHA 只表示历史验证基线，不冒充远程提交。`examples/test-results/submission-audit.json` 提供发布文件的 SHA-256 清单，可核对源码与材料是否一致。

环境文件不含 MODEL_BASE_URL；百炼通过 `MODEL_PROVIDER=bailian` 自动路由。真实密钥只存在本机被忽略的 .env，公开仓库只提交 .env.example。
