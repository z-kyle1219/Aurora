# Aurora 测试状态

> 只记录当前状态。验收记录见 `runs/`，变更历史见 `git log`。

## 当前阶段

| 阶段 | 状态 | 依据 |
|---|---|---|
| 阶段 0 测试基础设施 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 1 纯逻辑 P0 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 2 协议与编排 | `Not Executed` | 下一步 |
| 阶段 3 及之后 | `Not Executed` | 尚未整阶段验收；`AGT-INT-003` 因 DEF-02 验证提前完成 |

## 最近一次验收

- 日期：2026-11-21
- revision：`a10a10d`
- 验收记录：`runs/2026-11-21-stage1.md`

## 当前阻塞

- `pytest-cov` 未安装，覆盖率不可用
- 未关闭缺陷见 `DEFECTS.md`，按其「状态」列判断
- 阶段 2 准入前需定调 `RTR-UNIT-005/006` 与 `TEST_PLAN.md` §13 第 3 条的 Agent 失败路径契约

## 下一步

1. 阶段 2：Router / Rewrite / Judge / Agent 的畸形响应与失败路径
