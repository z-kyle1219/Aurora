# Aurora 测试状态

> 只记录当前状态。验收记录见 `runs/`，变更历史见 `git log`。

## 当前阶段

| 阶段 | 状态 | 依据 |
|---|---|---|
| 阶段 0 测试基础设施 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 1 纯逻辑 P0 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 2 结构化协议与失败路径 | `Passed` | `runs/2026-11-22-stage2.md` |
| 阶段 3 及之后 | `Not Executed` | 尚未整阶段验收；`AGT-INT-003` 因 DEF-02 验证提前完成 |

## 最近一次验收

- 日期：2026-11-22
- revision：`30d8cdb`
- 验收记录：`runs/2026-11-22-stage2.md`

## 当前阻塞

- `pytest-cov` 未安装，覆盖率不可用
- DEF-03（Router 非 dict 参数不兜底）、DEF-04（Router confidence 类型契约）为 `Open`，修复后需翻转对应 `xfail(strict=True)` 用例并登记关闭证据
- `run_agent()` 失败路径契约（`TEST_PLAN.md` §13 第 3 条）未决策，`AGT-UNIT-004` ①④ 按现状断言锁定
- 未关闭缺陷见 `DEFECTS.md`，按其「状态」列判断

## 下一步

1. 阶段 3：编排与多轮（`pipeline` / `agent→tools` / `rag` / `session/multi-turn`），准入含 `None`/占位文案的处置定案
2. 决策 `TEST_PLAN.md` §13 第 3 条；修复 DEF-03/04（不阻塞阶段 3 编写）
