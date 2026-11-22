# Aurora 测试状态

> 只记录当前状态。验收记录见 `runs/`，变更历史见 `git log`。

## 当前阶段

| 阶段 | 状态 | 依据 |
|---|---|---|
| 阶段 0 测试基础设施 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 1 纯逻辑 P0 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 2 结构化协议与失败路径 | `Passed` | `runs/2026-11-22-stage2.md` |
| 阶段 3 编排与多轮 | `Passed` | `runs/2026-11-22-stage3.md` |
| 阶段 4 及之后 | `Not Executed` | 尚未整阶段验收 |

## 最近一次验收

- 日期：2026-11-22
- revision：`d4ca11b`
- 验收记录：`runs/2026-11-22-stage3.md`

## 当前阻塞

- `pytest-cov` 未安装，覆盖率不可用
- DEF-03、DEF-04（Router 非 dict 参数 / confidence 类型契约）为 `Open`，修复后需翻转对应 `xfail(strict=True)`
- DEF-05~DEF-08（`store=None`、RAG `content=None`、轮数耗尽占位文案、截断带内容当成功）为 `Open`；契约已定案，修复后需翻转对应 `xfail` 并登记关闭证据
- `TEST_PLAN.md` §13 第 13 条（空索引降级文案是否写入历史）未决策，影响阶段 4 `PIP-E2E-002`
- 未关闭缺陷见 `DEFECTS.md`，按其「状态」列判断

## 下一步

1. 阶段 4：离线 E2E（`PIP-E2E-001/002`、`SES-E2E-001`、`RTR-E2E-001`）
2. 决策 `TEST_PLAN.md` §13 第 13 条
3. 修复 DEF-03~DEF-08；按 `xfail` 转 `XPASS` 的提示翻转用例并补关闭证据
