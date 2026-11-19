# Aurora 测试执行状态

> 状态日期：2026-11-19
>
> 阶段：阶段 1（Failed，待产品缺陷与计划冲突处理）

## 基线与环境

- Git revision：`876b39f04f66d0131fc31ff1888f7c7d46015f2e`（测试实施前基线；当前变更未提交）
- Python / pytest：Python 3.14.2 / pytest 9.2.0
- 网络：禁用
- 真实 LLM：禁用
- 真实 BGE：禁用
- 真实索引与课件：未读取

## 阶段 1 执行记录

本阶段新增 16 个稳定 Test ID，对应 47 个 pytest node id；连同阶段 0 的 8 个基础设施节点，默认套件共收集 55 个节点。所有正式执行均使用工作区 `.uv-cache`，不提供真实 API key，并由阶段 0 三层防线阻止 OpenAI、Socket 和 BGE 外部访问。由于受限沙箱无权创建 pytest 系统临时目录，两个使用 `tmp_path` 的用例先出现环境性 setup error；随后获准在沙箱外执行相同离线命令，正式结果不再包含该环境错误。

| 命令 | 退出码 | 实际结果 |
|---|---:|---|
| `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest --collect-only -q` | 0 | `55 tests collected in 0.03s`；Stage 1 的 16 个 Test ID 全部可追溯 |
| `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -q` | 1 | `51 passed, 3 failed, 1 xfailed in 0.33s`；无 skipped/xpassed/deselected |
| `$env:UV_CACHE_DIR='.uv-cache'; uv run pytest -m "not online and not ai_eval and not slow" -q` | 1 | `51 passed, 3 failed, 1 xfailed in 0.27s`；结果与默认过滤一致 |
| `uv run pytest --cov=aurora --cov-report=term-missing` | 未执行 | 当前环境未安装 `pytest-cov`，`pytest --help` 不包含 `--cov`；按任务约束未修改依赖 |

Stage 1 Test ID 状态：

| 状态 | 数量 | Test ID |
|---|---:|---|
| Passed | 13 | CFG-UNIT-001；CHK-UNIT-001/002；CRD-UNIT-001；VST-UNIT-001/002/003；VST-INT-001；SES-UNIT-001；TOL-UNIT-001/005/007/009 |
| Failed | 2 | TOL-UNIT-002（`a=1` 实际抛 `SyntaxError`，与计划的 `ValueError` 不符）；TOL-UNIT-006（两个参数节点分别逃逸 `TypeError`、`SyntaxError`） |
| XFailed | 1 | TOL-UNIT-003（`strict=True`，DEF-01；当前错误返回 `"3"`，目标为 `"3.14"`） |
| Blocked / Not Executed | 0 | 无 |

节点统计与 Test ID 统计不可混用：Stage 1 的 47 个节点中 43 passed、3 failed、1 xfailed；全套 55 个节点中 51 passed、3 failed、1 xfailed。阶段 1 未达到“全部 passed 或计划授权 xfailed”的准入条件。

覆盖率：`Not Available`。原因是当前项目未声明或安装 `pytest-cov`，本轮按要求未擅自新增依赖，不能提供或推测覆盖率百分比。

计划核对发现两处需人工评审的事实问题，未直接修改 `TEST_PLAN.md`：

1. TOL-UNIT-001 中 `3**4+sqrt(2)*sqrt(8)` 的手算结果是 `85.0`，计划写为 `97.0`；测试按数学正确值断言。
2. TOL-UNIT-002 将 `a=1` 列为应抛 `ValueError` 且称已实测，但实际 `ast.parse(..., mode="eval")` 抛 `SyntaxError`；该行为也落入 TOL-UNIT-006 要求统一转换的异常边界。

## 阶段 0 执行记录

执行环境使用工作区 `.uv-cache` 作为 `UV_CACHE_DIR`，并从验收命令环境移除 `DEEPSEEK_API_KEY`。测试启动时先固定 `stage-0-dummy-key`，再在受控窗口导入 `config.py` 并拦截 `load_dotenv()`；启动断言和 `INF-UNIT-008` 会在导入顺序或屏蔽失效时失败。未启用任何在线、模型或评测开关，未读取或使用真实 API Key。

| 命令 | 退出码 | 实际结果 |
|---|---:|---|
| `Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue; uv run pytest --collect-only -q` | 0 | 收集 `INF-UNIT-001` 至 `INF-UNIT-008` 对应的 8 个 node id；`8 tests collected in 0.01s` |
| `Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue; uv run pytest -q` | 0 | `8 passed in 0.02s`；审计复跑未再出现 warning |
| `git diff --check` | 0 | 未发现空白错误；输出 `docs/codebase-overview.md` 的 LF→CRLF warning |
| `git status --short` | 0 | `M`：`docs/codebase-overview.md`、`docs/testing/TESTING_GUIDE.md`、`docs/testing/TEST_PLAN.md`、`pyproject.toml`；`??`：`docs/testing/DEFECTS.md`、`docs/testing/TEST_STATUS.md`、`tests/` |

警告：pytest 曾在当前 Windows 工作区更新 `.pytest_cache` 时报告 `PytestCacheWarning` 和 `WinError 183`，属缓存状态的偶发现象；审计复跑 4 次均未复现，且从未影响用例收集、执行或退出码，也没有通过禁用插件、`skip`、`xfail` 或降低断言规避。`git diff --check` 另提示 `docs/codebase-overview.md` 下次由 Git 处理时可能从 LF 转为 CRLF，但退出码为 0，未发现空白错误。

整改过程记录：首次将隔离逻辑迁移到 `pytest_configure` 时，hook 参数误命名为 `pytest_config`，导致 `--collect-only` 和默认套件均在插件校验阶段退出码 1，未收集或执行用例，也未触发外部访问。修正为 pytest 规定的 `config` 参数后，以上表格中的正式复验全部通过。

阶段 0 自检取得以下独立证据：

- `INF-UNIT-001`：测试内 monkeypatch dummy key 后调用 `aurora.config.get_client()`，命中 OpenAI 构造哨兵并抛出 `OfflineProtectionError`；网络尝试记录为空。
- `INF-UNIT-002`：用受控 OpenAI 构造探针让 `aurora.config.get_client()` 进入 `socket.create_connection()`，命中 socket 哨兵并抛出 `OfflineProtectionError`；构造参数只含 dummy key 和项目 base URL。
- `INF-UNIT-003`：调用 `aurora.embedder.encode_query("probe")`，在模型构造或下载前命中 BGE 哨兵并抛出 `OfflineProtectionError`。
- `INF-UNIT-004`：实际请求 `tiny_vector_data` Fixture，成功生成 `float32` 的 `(3, 2)` 向量及字段符合真实 `Chunk(text, source, heading)` 定义的合成知识。
- `INF-UNIT-005`：在实际使用方 `aurora.router.get_client` 注入 `ScriptedClient`，验证响应、调用次数、消息、`model`、`temperature`、`tools` 和 `tool_choice`；脚本队列完全消费。
- `INF-UNIT-006`：直接调用业务模块已绑定的 `router.get_client()`，确认它与 `config.get_client` 是同一引用并仍命中 `config.OpenAI` 哨兵。
- `INF-UNIT-007`：对保留测试地址调用 `socket.socket.connect()`，确认该入口独立命中 socket 哨兵；socket 同时设置 1 秒超时作为失效兜底。
- `INF-UNIT-008`：断言进程测试环境和 `config.DEEPSEEK_API_KEY` 都是 dummy key，且受控导入期间 `load_dotenv()` 拦截恰好发生一次。

## 范围状态

- 阶段 0：`Passed`（8 collected / 8 passed / 0 failed / 0 skipped / 0 xfailed / 0 deselected）
- 阶段 1：`Failed`（16 Test ID：13 Passed / 2 Failed / 1 XFailed；47 nodes：43 passed / 3 failed / 1 xfailed）
- 阶段 2 及之后：`Not Executed`，不属于本次实施范围
- `online` / `ai_eval` / `slow`：`Not Executed`，默认 marker 过滤

## 缺陷与阻塞

- 新增缺陷：DEF-01、DEF-02
- 关闭缺陷：无
- 阻塞项：`pytest-cov` 未安装，无法生成覆盖率；DEF-02 未修复且本轮禁止修改业务代码

## 与上次基线的变化

- 建立 pytest marker 与默认离线过滤。
- 建立 OpenAI 客户端、socket 网络和 BGE 模型加载三层防线。
- 建立严格脚本 Fake、响应构造器、微型向量与合成知识数据。
- 建立阶段 0 自检及缺陷、执行状态文档。
- 根据独立审计拆分三层防线自检，修正 `Chunk` Fixture，消除本机 `.env`/真实 key 依赖，并登记稳定 Test ID。
- 增加 `socket.socket.connect`、业务模块绑定引用与 dotenv 导入顺序回归保护；删除未使用的 `MALFORMED_ARGUMENTS`。

## 下一步

人工审核 DEF-01、DEF-02 与两处测试计划事实冲突；在业务修复或契约决策完成前，不进入阶段 2。本轮未修改业务代码，也未提交 Git。
