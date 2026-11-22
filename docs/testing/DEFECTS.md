# Aurora 测试缺陷登记

> 建立日期：2026-11-19
>
> 当前范围：阶段 0 测试基础设施、阶段 1 纯逻辑 P0、阶段 2 结构化协议与失败路径

阶段 0 未新增产品缺陷。阶段 1 按测试计划定标并复现 DEF-01、DEF-02；阶段 2 按 `TEST_PLAN.md` §4.4 A 类约定登记 DEF-03、DEF-04（目标行为已在计划 §6.3 定稿，修复前以 `xfail(strict=True)` 定标）。禁止以放宽断言或无依据的 `skip` 代替缺陷记录。

「当前行为」写修复后的实际表现；缺陷只有在关联 Test ID 通过正式验收后才能标记 `Closed`。「关闭证据」只写判定结果与对应 run 文件路径，执行统计、环境与失败明细一律以该文件为准。

| 缺陷 ID | 关联 Test ID | 优先级 | 当前行为 | 目标行为 | 复现命令 | 首次发现 revision | 状态 | 负责人/决策人 | 关闭证据 |
|---|---|---|---|---|---|---|---|---|---|
| DEF-01 | TOL-UNIT-003 | P0 | `execute_tool()` 返回 `计算失败：不支持关键字参数，请改用位置参数，例如 round(3.14159, 2)`；位置参数 `round(3.14159, 2)` 返回 `"3.14"` | 关键字参数生效并返回 `"3.14"`，或显式拒绝而非静默改变计算语义 | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q` | `876b39f04f66d0131fc31ff1888f7c7d46015f2e` | `Closed` 2026-11-21 | kyle（项目作者） | `TOL-UNIT-003` 通过；证据见 `runs/2026-11-21-stage1.md` |
| DEF-02 | TOL-UNIT-006、AGT-INT-003 | P0 | `execute_tool()` 把预期计算与语法错误转成 `计算失败：…` 字符串，不向外抛出：非实数运算 → `计算失败：计算结果不是实数`，语法错误 → `计算失败：表达式语法错误`，参数不匹配 → `计算失败：参数不匹配，请检查函数参数个数与类型`；`run_agent()` 正常回传 tool 消息并继续到最终回答 | 非实数结果由计算层以稳定 `ValueError` 拒绝；`execute_tool()` 将预期计算/语法错误转换为模型可读字符串；`run_agent()` 正常回传 tool 消息、记录 trace 并继续到最终回答 | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q tests/unit/test_tools.py tests/integration/test_agent_tools.py` | `876b39f04f66d0131fc31ff1888f7c7d46015f2e` | `Closed` 2026-11-21 | kyle（项目作者） | `TOL-UNIT-006`、`AGT-INT-003` 通过；证据见 `runs/2026-11-21-stage1.md` |
| DEF-03 | RTR-UNIT-005 | P0 | `route()` 对解析成功但非 dict 的 `arguments`（`[]`、`"opamp"`、`null`）执行 `args.get` 时抛 `AttributeError`，异常穿出 Router 中断 Pipeline，与其他坏输出（无 tool_call、坏 JSON 均回退 `concept / 0.0`）行为不一致 | 与其他坏输出一致回退：`circuit_type` 回退 `concept`、`confidence` 回退 `0.0`、`reason` 为可识别的非空文案，不向外抛异常 | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q tests/unit/test_router.py` | `84207bb114d60fc8b434f68425ca3244e12405c3` | `Open` | kyle（项目作者） | — |
| DEF-04 | RTR-UNIT-006 | P0 | `route()` 对非数值 `confidence` 不兜底：`"high"` 抛 `ValueError`、`null` 抛 `TypeError`，均穿出 Router 中断 Pipeline；越界数值 `2.5`/`-1` 不钳位、原样透出；缺失字段回退 `0.0` | 非数值 `confidence` 统一回退 `0.0`，数值钳位到 `[0, 1]`（`2.5` → `1.0`、`-1` → `0.0`），不向外抛异常；缺失字段维持 `0.0` | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q tests/unit/test_router.py` | `84207bb114d60fc8b434f68425ca3244e12405c3` | `Open` | kyle（项目作者） | — |

DEF-01 整改的连带变更（不另立缺陷）：`execute_tool()` 同时增加 `TypeError` 捕获——`sqrt()` 这类参数个数不匹配的错误与 DEF-02 属同一类问题（预期错误必须留在工具边界），因此随本次整改一并处理。边界仍按白名单放行，未放宽 AST 规则。
