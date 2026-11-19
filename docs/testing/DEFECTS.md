# Aurora 测试缺陷登记

> 建立日期：2026-11-19
>
> 当前范围：阶段 0 测试基础设施、阶段 1 纯逻辑 P0

阶段 0 未新增产品缺陷。阶段 1 按测试计划定标并复现以下产品缺陷；禁止以放宽断言或无依据的 `skip` 代替缺陷记录。

| 缺陷 ID | 关联 Test ID | 优先级 | 当前行为 | 目标行为 | 复现命令 | 首次发现 revision | 状态 | 负责人/决策人 | 关闭证据 |
|---|---|---|---|---|---|---|---|---|---|
| DEF-01 | TOL-UNIT-003 | P0 | `round(3.14159, ndigits=2)` 的关键字参数被静默丢弃，`execute_tool()` 返回 `"3"` | 关键字参数生效并返回 `"3.14"`，或显式拒绝而非静默改变计算语义 | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q tests/unit/test_tools.py -k tol_unit_003` | `876b39f04f66d0131fc31ff1888f7c7d46015f2e` | Open / XFailed | 待人工决策 | 无 |
| DEF-02 | TOL-UNIT-006、AGT-INT-003 | P0 | `execute_tool()` 未捕获非实数计算产生的预期错误和表达式 `SyntaxError`，异常会逃出 `run_agent()`，无法形成 tool 消息和 trace | 非实数结果由计算层以稳定 `ValueError` 拒绝；`execute_tool()` 将预期计算/语法错误转换为模型可读字符串；`run_agent()` 正常回传 tool 消息、记录 trace 并继续到最终回答 | `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q tests/unit/test_tools.py tests/integration/test_agent_tools.py` | `876b39f04f66d0131fc31ff1888f7c7d46015f2e` | Closed | 待人工决策 | 2026-11-19 17:21:57 +08:00（HEAD `a652851`），退出码 0：`33 passed, 1 xfailed in 0.25s`；TOL-UNIT-006 两个参数节点与 AGT-INT-003 均通过。`a=1` 属已修正的测试契约偏差，不计入本产品缺陷。 |
