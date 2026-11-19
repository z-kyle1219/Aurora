# Aurora 测试执行状态

> 状态日期：2026-11-19
>
> 阶段：阶段 0（Passed）

## 基线与环境

- Git revision：`22cd1865f56245510f61c3ce8c5a33f14ee02ad1`
- Python / pytest：Python 3.14.2 / pytest 9.2.0
- 网络：禁用
- 真实 LLM：禁用
- 真实 BGE：禁用
- 真实索引与课件：未读取

## 执行记录

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
- 阶段 1 及之后：`Not Executed`，不属于本次实施范围
- `online` / `ai_eval` / `slow`：`Not Executed`，默认 marker 过滤

## 缺陷与阻塞

- 新增缺陷：无
- 关闭缺陷：无
- 阻塞项：无

## 与上次基线的变化

- 建立 pytest marker 与默认离线过滤。
- 建立 OpenAI 客户端、socket 网络和 BGE 模型加载三层防线。
- 建立严格脚本 Fake、响应构造器、微型向量与合成知识数据。
- 建立阶段 0 自检及缺陷、执行状态文档。
- 根据独立审计拆分三层防线自检，修正 `Chunk` Fixture，消除本机 `.env`/真实 key 依赖，并登记稳定 Test ID。
- 增加 `socket.socket.connect`、业务模块绑定引用与 dotenv 导入顺序回归保护；删除未使用的 `MALFORMED_ARGUMENTS`。

## 下一步

阶段 1 及后续业务测试仍为 `Not Executed`，应在阶段 0 通过审核后按 `TEST_PLAN.md` 顺序另行实施。
