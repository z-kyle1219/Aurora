# Aurora 测试状态

> 只记录当前状态。验收记录见 `runs/`，变更历史见 `git log`。

## 当前阶段

| 阶段 | 状态 | 依据 |
|---|---|---|
| 阶段 0 测试基础设施 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 1 纯逻辑 P0 | `Passed` | `runs/2026-11-21-stage1.md` |
| 阶段 2 结构化协议与失败路径 | `Passed` | `runs/2026-11-22-stage2.md` |
| 阶段 3 编排与多轮 | `Passed` | `runs/2026-11-22-stage3.md` |
| 阶段 4 离线 E2E | `Passed` | `runs/2026-11-22-stage4.md` |
| 阶段 5 AI Evaluation | `Needs Review` | `runs/2026-11-22-stage5-meta.md`；仅元评估已执行，整阶段未验收 |
| 阶段 6 CI | `Not Executed` | 尚未实施 |

## 最近一次验收

- 日期：2026-11-22
- revision：`fe8206a`
- 验收记录：`runs/2026-11-22-stage4.md`

## 当前阻塞

- `pytest-cov` 未安装，覆盖率不可用
- `EVL-META-001` 修正口径后已在线执行：幻觉二分类 agreement 100%、kappa 1.000；三档判分 agreement 82.5%、kappa 0.738，仍有 7 条分歧且缺少异源裁判对照；详见 `runs/2026-11-22-stage5-meta.md`
- DEF-03、DEF-04（Router 非 dict 参数 / confidence 类型契约）为 `Open`，修复后需翻转对应 `xfail(strict=True)`
- DEF-05~DEF-08（`store=None`、RAG `content=None`、轮数耗尽占位文案、截断带内容当成功）为 `Open`；契约已定案，修复后需翻转对应 `xfail` 并登记关闭证据
- 未关闭缺陷见 `DEFECTS.md`，按其「状态」列判断

## 下一步

1. 阶段 5：执行异源裁判对照，决定三档判分对“核心正确但夹带错误”的统一尺度；确认裁判口径后再建立质量基线与门槛。其余评估作业尚未实施
2. 修复 DEF-03~DEF-08；按 `xfail` 转 `XPASS` 的提示翻转用例并补关闭证据
