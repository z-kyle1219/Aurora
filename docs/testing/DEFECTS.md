# Aurora 测试缺陷登记

> 建立日期：2026-11-19
>
> 当前范围：阶段 0 测试基础设施、阶段 1 纯逻辑 P0

阶段 0 未新增产品缺陷。阶段 1 按测试计划定标并复现以下产品缺陷；禁止以放宽断言或无依据的 `skip` 代替缺陷记录。

「当前行为」写修复后的实际表现；缺陷只有在关联 Test ID 通过正式验收后才能标记 `Closed`。「关闭证据」只写判定结果与对应 run 文件路径，执行统计、环境与失败明细一律以该文件为准。

| 缺陷 ID | 关联 Test ID | 优先级 | 当前行为 | 目标行为 | 复现命令 | 首次发现 revision | 状态 | 负责人/决策人 | 关闭证据 |
|---|---|---|---|---|---|---|---|---|---|
| DEF-01 | TOL-UNIT-003 | P0 | `execute_tool()` 返回 `计算失败：不支持关键字参数，请改用位置参数，例如 round(3.14159, 2)`；位置参数 `round(3.14159, 2)` 返回 `"3.14"` | 关键字参数生效并返回 `"3.14"`，或显式拒绝而非静默改变计算语义 | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q` | `876b39f04f66d0131fc31ff1888f7c7d46015f2e` | `Fixed, Not Verified` | kyle（项目作者） | 等待新证据基线验收 `TOL-UNIT-003` |
| DEF-02 | TOL-UNIT-006、AGT-INT-003 | P0 | `execute_tool()` 把预期计算与语法错误转成 `计算失败：…` 字符串，不向外抛出：非实数运算 → `计算失败：计算结果不是实数`，语法错误 → `计算失败：表达式语法错误`，参数不匹配 → `计算失败：参数不匹配，请检查函数参数个数与类型`；`run_agent()` 正常回传 tool 消息并继续到最终回答 | 非实数结果由计算层以稳定 `ValueError` 拒绝；`execute_tool()` 将预期计算/语法错误转换为模型可读字符串；`run_agent()` 正常回传 tool 消息、记录 trace 并继续到最终回答 | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q tests/unit/test_tools.py tests/integration/test_agent_tools.py` | `876b39f04f66d0131fc31ff1888f7c7d46015f2e` | `Fixed, Not Verified` | kyle（项目作者） | 等待新证据基线验收 `TOL-UNIT-006`、`AGT-INT-003` |

DEF-01 整改的连带变更（不另立缺陷）：`execute_tool()` 同时增加 `TypeError` 捕获——`sqrt()` 这类参数个数不匹配的错误与 DEF-02 属同一类问题（预期错误必须留在工具边界），因此随本次整改一并处理。边界仍按白名单放行，未放宽 AST 规则。
