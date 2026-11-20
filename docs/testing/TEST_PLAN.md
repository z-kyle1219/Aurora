# Aurora 测试总计划

> 审计日期：2026-11-18

> 审计基线：`main` @ `9fec2c2`（`docs/testing/` 目录未被 Git 跟踪）

> 审计方式：静态阅读全部 `src/`、`scripts/`、配置与文档，并用离线一次性探针（不联网、不调 API）验证关键行为。

> 执行声明：**本次审计没有运行任何测试套件**。审计时仓库中没有 `tests/`，且 pytest 尚未纳入项目依赖；当前 pytest 已作为开发依赖安装并锁定，但仍没有可执行测试用例。本文不含任何"已通过"结论。

> 本文是**测试设计资产**，不是测试实现。所有 `Planned` 条目均未写代码。

## 0. 状态定义与本次修订说明

状态取值：

| 状态 | 含义 |

|---|---|

| `Planned` | 当前无对应用例，建议按本文设计实现 |

| `Existing / Not Executed` | 已有脚本或断言覆盖该场景，但本次未执行，不能视为通过 |

| `Not Implemented` | 产品能力不存在，仅记录未来启用条件 |

| `N/A` | 该层级对当前职责无直接价值 |

相比 `9fec2c2` 时点的初版计划，本次交叉验证修正了三处**事实错误**，并新增两处**安全缺口**：

1. Router 的 `confidence: null` 抛的是 `TypeError`（`float(None)`），不是 `ValueError`。

2. `openai` 实际安装版本为 `3.37.0`，默认 `connect=5s / read=600s / max_retries=2`，已核实而非引用通用常识。

3. **历史审计状态（`9fec2c2` 时点）**：工具层 `execute_tool()` 只捕获 `ValueError / ZeroDivisionError / OverflowError`；`(-8)**(1/3)` 会产生 `complex`，`float(complex)` 抛 **`TypeError` 逃逸**，会直接中断 opamp 链路（当时新发现，P0）。当前实现已在 `calculate_expression()` 中以稳定 `ValueError` 拒绝非实数结果，并由 `execute_tool()` 转换为模型可读错误字符串。

4. `calculate_expression("True")` 返回 `1`（`bool` 是 `int` 子类，命中了 `Constant` 白名单），属于类型放宽，需要显式固定或收紧（新发现，P2）。

---

## 1. 项目测试目标与边界

### 1.1 目标

1. **保护确定性逻辑**：AST 计算白名单、切块清洗、向量排序、Session 写回、Router/Rewrite/Judge 的结构化解析与兜底。

2. **保护跨模块契约**：`pipeline.run()` 的入参/返回 dict、`ask_rag()` 的 `(answer, hits)`、`run_agent()` 的 `(answer, trace)`、`chunk/vector` 的位置对应关系。

3. **保护失败路径**：外部模型异常、空回答、轮数耗尽、坏索引、`store=None` 时系统**不会伪装成功**、不会污染会话状态。

4. **量化 AI 能力**：把 Router 准确率、检索 Recall/MRR、答案忠实度、工具调用准确率、多轮上下文保真拆成**独立、可重复、可留档**的评估作业，与软件回归测试分离。

5. **阻止意外副作用**：默认 `pytest` 不得访问 DeepSeek、不得下载 BGE 模型、不得写入 `data/index/`。

### 1.2 明确不在范围内

以下能力在当前代码中**不存在**，不为它们设计"通过/失败"用例，仅在矩阵中标注 `N/A` 或 `Not Implemented`：

- Web / API / GUI 入口（只有命令行脚本）

- 完整 Pipeline 的流式输出（`companion_v0.ask_stream` 不经过 Pipeline）

- Session 持久化、session id、跨进程共享、并发安全

- 日志、监控、统一异常层、请求重试/熔断

- 相似度阈值、混合检索、rerank、metadata filter

- 通用工具注册表、外部工具、并行工具执行

- 向量数据库（没有 Chroma/FAISS；`VectorStore` 是 NumPy 内存矩阵）

并发只作为**风险观测**（`SES-RISK-001`），不作为验收门槛。

---

## 2. 项目实际架构与核心模块

### 2.1 真实模块清单（全部来自 `src/aurora/`，无虚构）

| 文件 | 真实公开接口 | 职责 | 外部依赖 | 性质 |

|---|---|---|---|---|

| `config.py` | `get_client()`、模块常量 `DEEPSEEK_API_KEY/BASE_URL/MODEL` | 导入时 `load_dotenv()`，每次调用新建 `OpenAI` 客户端 | env、网络 | 运行时冻结 |

| `rewrite.py` | `rewrite_question()`、`_format_history()` | 有历史时把追问改写为独立问题 | LLM | 概率性 + 确定性兜底 |

| `router.py` | `route()`、`CIRCUIT_TYPES`、`TYPE_LABELS`、`ROUTE_TOOL` | 强制 function call 输出五类标签 | LLM | 概率性 + 确定性解析 |

| `pipeline.py` | `run()`、`fallback_answer()`、`RAG_PROMPTS`、`OPAMP_VALIDATORS`、`SUPPLY_VOLTAGE` | 编排、分支提示词、Session 收口 | 下游全部 | 确定性编排 |

| `rag.py` | `retrieve()`、`build_context()`、`ask_rag()` | 查询向量化 → top-k → 拼上下文 → 生成 | Embedding、LLM | 检索确定性 + 生成概率性 |

| `agent.py` | `run_agent()` | 工具调用回环（最多 5 轮）、validator 钩子、trace | LLM、tools | 协议确定 + 决策概率 |

| `tools.py` | `calculate_expression()`、`execute_tool()`、`validate_opamp_output()`、`TOOLS` | AST 白名单计算、工具分派、运放电压校验 | 无（纯本地） | **完全确定性** |

| `session.py` | `Session`（`add_turn/context/__len__`） | 进程内历史，问答成对写回 | 无 | 完全确定性 |

| `chunker.py` | `chunk_text()`、`chunk_file()`、`Chunk` | 清洗课件、600 字封口、标题继承 | 文件系统 | 完全确定性 |

| `card_loader.py` | `load_cards()` | `##` 二级标题 → 每卡一 `Chunk` | 文件系统 | 完全确定性 |

| `embedder.py` | `encode_passages()`、`encode_query()`、`get_model()` | BGE 向量化（查询加前缀） | sentence-transformers、模型下载 | 外部模型 |

| `vector_store.py` | `VectorStore`、`SearchResult` | 点积 top-k、`.npy` + `.json` 持久化 | NumPy、文件系统 | 完全确定性 |

| `judge.py` | `judge_answer()`、`judge_hallucination()` | LLM-as-judge 三档判分 / 布尔幻觉 | LLM | 概率性 |

| `companion_v0.py` | `ask()`、`ask_stream()` | W1 直连聊天，**不经过 Pipeline** | LLM | 概率性 |

脚本层：`build_index.py`（离线建库）、`demo_rag.py` / `demo_tools.py` / `demo_router.py` / `demo_multi_turn.py`（演示）、`eval.py` / `eval_report.py`（在线评测）、`chat_loop.py`（走 `companion_v0`，与主链分离）。

### 2.2 数据与配置现况（已核实）

- `data/index/`：**24 个 Chunk**，其中课件 16 块、公式卡片 8 块；来源覆盖 `6.1 / 6.3 / 7.1 / 7.2 / 7.3 / 7.4 / 7.5(1) / 9.1 / 9.3 / 9.4` 与 8 张卡片。目录被 `.gitignore` 忽略。

- `data/eval_set.json`：**12 条**，`opamp` 2 / `feedback` 4 / `power` 2 / `concept` 2 / `fallback` 2。

- 建库输入 `../courseware_md/*.md` **位于仓库之外**，`knowledge/pages/` 切图被忽略。

- 依赖：Python `3.14.2`（`.venv` 实测）、`openai 3.37.0`、`numpy`、`sentence-transformers`、`python-dotenv`；当前已声明并安装 `pytest 9.2.0` 开发依赖，仍无 lint、类型检查或 CI 配置。

---

## 3. 核心业务调用链路

### 3.1 主链

```text

pipeline.run(question, store, top_k=3, *, session=None)

  │

  ├─ session = session or Session()          # 不传 = 临时单轮，静默丢弃

  ├─ history = session.context()             # 返回内部 list 本体，不是副本

  ├─ standalone = rewrite_question(question, history)

  │     └─ history 为空 → 原样返回，零模型调用

  ├─ route_info = route(standalone)          # deepseek tool_choice 强制 route_circuit

  │

  ├─ opamp    → run_agent(question, validators=OPAMP_VALIDATORS, history=history)

  │               └─ execute_tool → calculate_expression → validate_opamp_output

  ├─ fallback → fallback_answer(question)    # 不检索、不调模型

  └─ feedback/power/concept

              → ask_rag(question, store, top_k, history=history,

                        query=standalone, system_prompt=RAG_PROMPTS.get(type))

                    └─ encode_query → VectorStore.search → build_context → deepseek

  │

  ├─ session.add_turn(question, answer)      # 写原话 + 分支返回的 answer

  └─ return {circuit_type, confidence, reason, standalone, answer, trace, hits}

```

### 3.2 分支与外部调用次数（首轮 / 有历史时）

| 分支 | 处理 | 首轮 LLM 调用 | 多轮 LLM 调用 | `trace` | `hits` |

|---|---|---|---|---|---|

| `opamp` | Tool Agent + 电压校验 | 1 + N（N≥1，上限 5） | 2 + N | `list` | `None` |

| `feedback` | RAG + 决策树提示词 | 2 | 3 | `None` | `list` |

| `power` | RAG + 链式流程提示词 | 2 | 3 | `None` | `list` |

| `concept` | RAG + 默认提示词 | 2 | 3 | `None` | `list` |

| `fallback` | 模板文案 | 1 | 2 | `None` | `None` |

（"2"= Router + 生成；多轮多一次改写。`opamp` 的改写结果只被 Router 消费，属纯开销。）

### 3.3 状态写回的真实规则

- 写回点唯一：`pipeline.run()` 尾部 `session.add_turn()`，五条分支共用。

- 抛异常发生在写回之前 → Session 长度不变（`rewrite.py` 的异常在内部已吞掉，不中断）。

- **但**以下两种"假成功"仍会被写回：

  1. `ask_rag()` 在 `message.content is None` 时返回 `None`，`add_turn()` 会把 `None` 存进历史，下一轮把 `None` 作为 `content` 发给模型；

  2. `run_agent()` 达 `max_rounds` 返回占位文案 `（已达到最大工具调用轮数，仍未得到最终回答）`，Pipeline 无法区分它与正常答案。

- 空索引时 `ask_rag()` 返回"课件库还是空的…"字符串，同样被当作正常答案写回。

- `standalone` 只进 Router 与检索，不进生成、不进 Session。

---

## 4. 测试分类与测试策略

### 4.1 四个层级（Aurora 版定义）

| 层级 | 判定标准 | 允许的 Mock | 默认是否联网 |

|---|---|---|---|

| Unit | 只测一个模块内的函数/类；不跨模块 | 全部外部依赖 | 否 |

| Integration | 至少跨 2 个真实模块，被验证的协作边界保持真实 | 仅模型/网络边界 | 否 |

| E2E | 从 `pipeline.run()` 入参到统一 dict + Session 写回 | 仅模型边界；Pipeline/Session/tools/VectorStore 全真 | 离线 E2E 否，在线 E2E 是 |

| AI Eval | 用固定数据集算聚合指标 | 不 Mock | 是（独立作业） |

关键区分：**软件测试断言"行为契约"，AI 评估断言"质量分布"**。Router 解析结构是 Unit，Router 分类准不准是 AI Eval；两者的失败处置完全不同（前者阻塞合并，后者只出报告）。

### 4.2 十个策略问题的答案

1. **可完全独立运行**：`tools`、`chunker`、`card_loader`、`vector_store`（除 save/load 用 `tmp_path`）、`session`、`pipeline.fallback_answer`、`rewrite._format_history`、`judge` 的解析分支、`eval.py` 的 `is_correct` / `eval_report.py` 的 `citation_hit_rate` 与 `majority`。这些不碰网络、不碰模型。

2. **需要 Mock LLM**：`router.route`、`rewrite.rewrite_question`、`rag.ask_rag`、`agent.run_agent`、`judge.*`、`companion_v0.*`，以及任何 E2E。

3. **需要 Mock Embedding**：`rag.retrieve` / `ask_rag` 的集成测试（patch `aurora.rag.encode_query` 返回固定向量）；`embedder` 自身的调用契约测试（patch `aurora.embedder.get_model`）。只有检索质量评估用真模型。

4. **需要真实向量数据库**：**不需要**。项目没有外部向量库。软件测试用内存 `VectorStore`，持久化用 `tmp_path`。AI Eval 用版本化冻结索引。

5. **需要真实 LLM API**：仅 `PIP-E2E-003`、全部 `*-AI-*`、`EVL-META-001`，以及手工入口 `LGC-E2E-001`（不入 CI），且必须显式 marker + 环境变量双重开关。

6. **避免默认产生真实调用与费用**（三层防线，落点各不相同）：

   - `pyproject.toml` 配置 `addopts = "-m 'not online and not ai_eval and not slow'"`；

   - **全局熔断**：autouse patch `aurora.config.OpenAI` 为"构造即抛错"的替身。`get_client()` 的函数体在调用时才从 `config` 模块全局解析 `OpenAI`，因此**各模块即便用 `from aurora.config import get_client` 绑定了本地引用，这条路依然被拦住**（已离线实测：patch 后 `aurora.router.get_client()` 返回替身而非真实客户端）。

   - **网络兜底**：autouse patch `socket.socket.connect` 与 `socket.create_connection` 抛 `RuntimeError`，覆盖绕过客户端的直连（如 BGE 权重下载）。

   - **按测试注入**：需要 fake 响应或断言"未调用"时，再 patch 使用方模块的 `get_client`（详见 §4.3）。

   - 另对 `aurora.embedder.get_model` 做 autouse patch，防止首次下载 100MB。

7. **隔离外部依赖**：

   - 文件系统：一律 `tmp_path`；禁止写 `data/index/`、`.env`、`../courseware_md/`；对 `VectorStore.save/load` 使用显式目录参数而不是模块常量。

   - 数据库：无。

   - 环境变量：`config.py` 在导入时冻结常量，因此**不要靠 `monkeypatch.setenv` 改行为**（导入顺序会污染），直接 `monkeypatch.setattr("aurora.config.DEEPSEEK_API_KEY", None)`。

   - 模型缓存：`embedder.get_model` 带 `lru_cache(maxsize=1)`，相关用例前后必须 `get_model.cache_clear()`，否则 patch 会跨用例泄漏。

8. **测试互不影响**：每个用例新建 `Session` / `VectorStore` / fake client；fake client 用"响应队列按调用顺序弹出"，队列耗尽即抛错（能捕获多调用/少调用）；不复用可变 history；不共享模块级常量（除只读的 `CIRCUIT_TYPES`）。

9. **Fixture 与数据组织**：见 §8。原则是"软件测试的微型数据"与"AI Eval 的黄金集"物理分离，后者不进默认 CI。

10. **适合进 CI**：全部 Unit + Integration + 离线 E2E；`git diff --check`；`uv lock --check`。在线 E2E 与 AI Eval 用手动/定时工作流。

### 4.3 Mock 的正确落点（易错点）

`get_client` 在每个模块里是 `from aurora.config import get_client` 后**直接调用**，没有注入点。因此：

```python

# 注入 fake 并断言调用次数：patch 实际使用模块里的名字

monkeypatch.setattr("aurora.router.get_client", lambda: fake)

# 无效：已绑定的本地引用不会因源头改名而更新

monkeypatch.setattr("aurora.config.get_client", lambda: fake)

```

三种补丁的分工必须分清，否则要么拦不住，要么以为拦住了：

| 补丁目标 | 是否影响各模块 | 能拿到什么 | 用途 |

|---|---|---|---|

| `aurora.config.get_client` | **否**（已绑定本地引用） | — | 无效，不要用 |

| `aurora.config.OpenAI` | **是**（`get_client` 调用时才解析该名字） | 只能"全局禁止/全局替换" | conftest 的 autouse 熔断 |

| `aurora.<module>.get_client` | 仅该模块 | fake 响应、调用次数与实参 | 单测/集成测试注入 |

同理 `aurora.rag.encode_query`、`aurora.agent.execute_tool`、`aurora.embedder.get_model` 都必须在**使用方模块**打补丁。

### 4.4 缺陷定标（pin）约定

P0 里有若干用例，其"当前行为"本身就是缺陷（关键字参数被丢弃、`content=None` 被写回、轮数耗尽被当成功……）。这类用例必须二选一，不能含糊，否则要么把 bug 锁死，要么让阶段准入永远红：

| 类型 | 断言什么 | pytest 写法 | 适用用例 |

|---|---|---|---|

| **A. 缺陷定标** | 断言**正确行为**（缺陷存在时必然失败） | `@pytest.mark.xfail(strict=True, reason="DEF-01 关键字参数被丢弃")` | 历史规划：RTR-UNIT-005、RTR-UNIT-006、RAG-INT-004、RAG-INT-005、PIP-INT-005；其中 AGT-INT-003 对应异常逃逸已整改、TOL-UNIT-003 已改为显式拒绝契约，两者不再适用此分类 |

| **B. 现状锁定** | 断言**当前行为**（含错误值），并注明修复时同步改断言 | 普通 `assert` + `# 修复 DEF-xx 后改此断言` | TOL-UNIT-004（`:g` 精度）、VST-UNIT-002（负 k）、CHK-UNIT-001（全角清洗）、SES-UNIT-002（返回内部 list） |

不用 `skip` 的理由：`skip` 会让缺陷永久沉默；`xfail(strict=True)` 在缺陷修复后转为 `XPASS` 并**直接报错**，强制作者回来删标记，缺陷不会在修复后继续"假装红着"。

每一项缺陷定标用例都要在 `reason` 里带缺陷编号；建议在 `docs/testing/DEFECTS.md` 建一张 "DEF-xx → 用例 ID → 决策项" 的对照表，避免缺陷修复后没人回头改测试。

---

## 5. 功能模块 × 测试层级矩阵

图例：`✓` 必须覆盖；`△` 少量/间接覆盖；`—` 不适用。

| Module | Unit | Integration | E2E | AI Evaluation | 理由 |

|---|:--:|:--:|:--:|:--:|---|

| `config` | ✓ | ✓ | △ | — | 只验证配置失败与客户端构造参数，无"能力"可评 |

| `rewrite` | ✓ | ✓ | △ | ✓ | 门控/截断/回退可确定；指代消解质量必须用数据集 |

| `router` | ✓ | ✓ | ✓ | ✓ | 解析与兜底确定；五类准确率/混淆矩阵是核心 AI 指标 |

| `chunker` | ✓ | ✓ | — | △ | 纯文本处理；"切块策略影响检索"属实验性评估 |

| `card_loader` | ✓ | ✓ | — | — | 纯解析，无模型参与 |

| `embedder` | ✓ | △ | — | ✓ | 调用契约可 Mock；语义质量归入检索评估 |

| `vector_store` | ✓ | ✓ | △ | △ | 排序/持久化确定；召回率用标注集衡量 |

| `rag`（在线链） | ✓ | ✓ | ✓ | ✓ | 上下文契约 + 忠实度/引用率都重要 |

| `tools` | ✓ | ✓ | △ | ✓ | 安全边界是 P0；"模型是否会正确调工具"属评估 |

| `agent`（回环） | ✓ | ✓ | ✓ | ✓ | 协议确定；工具选择与多步推理概率性 |

| `pipeline` | ✓ | ✓ | ✓ | △ | 编排本身不单独设 AI 分数，指标归各能力 |

| `session` / 多轮 | ✓ | ✓ | ✓ | ✓ | 状态正确性与上下文理解必须分开测 |

| `judge` / `eval*.py` | ✓ | ✓ | — | ✓（元评估） | 评测器自身可靠性需人工对齐 |

| `companion_v0` | △ | — | △ | — | 旧入口，只做冒烟，不投测试预算 |

| `build_index.py` | — | ✓ | — | — | 只验证可控语料下的流程与落盘 |

**不套用示例的原因**：`chunker` / `card_loader` / `vector_store` 没有"AI 能力"可评，给它们 AI Eval 只会制造无意义指标；`pipeline` 的 E2E 是**唯一**能同时验证"改写→路由→分支→写回"的层级，因此标 `✓` 而非 `—`。

---

## 6. 各模块详细测试用例

> 通用约定：`fake` = 一个实现了 `chat.completions.create(**kwargs)` 的替身，返回 `SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=..., tool_calls=...), finish_reason="stop")])`。所有 `Planned` 用例默认断网。

### 6.1 配置层 `config.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| CFG-UNIT-001 | config | Unit | 缺 key 时明确失败 | `monkeypatch` 置 `DEEPSEEK_API_KEY=None` | 抛 `RuntimeError`，消息含 `.env` 指引；不构造客户端 | 无 | patch 模块常量，不读真实 `.env` | P0 | Planned |

| CFG-UNIT-002 | config | Unit | 客户端构造参数 | 固定 key / base_url / model | `OpenAI` 收到 `api_key` 与 `base_url`；两次 `get_client()` 返回不同实例（当前不复用） | openai SDK | patch `aurora.config.OpenAI` 为记录参数的替身 | P1 | Planned |

| CFG-UNIT-003 | config | Unit | 超时与重试的真实默认值 | `get_client()` 未传 `timeout/max_retries` | 记录实际继承值（实测 openai 3.37.0：`connect=5s`、`read=600s`、`max_retries=2`）；未来显式配置后改为断言项目值 | openai 3.37.0 | 读 `openai._constants.DEFAULT_TIMEOUT` 与 `OpenAI.__init__` 签名 | P1 | Planned |

### 6.2 上下文改写 `rewrite.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| RWT-UNIT-001 | rewrite | Unit | 首轮零成本门控 | `history=None` / `[]` | 原样返回；`get_client` **未被调用**（用会抛异常的哨兵验证） | 无 | 不 patch，用哨兵 | P0 | Planned |

| RWT-UNIT-002 | rewrite | Unit | 历史窗口与截断 | 5 轮共 10 条；某条 400 字；role 为 `system` | 只取最后 4 条；长文本截到 300 字 + `…`；非 `user` 显示"Aurora"；换行被压成空格 | 无 | 直接测 `_format_history` | P1 | Planned |

| RWT-UNIT-003 | rewrite | Unit | 异常与异常输出回退 | ①`get_client` 抛 `RuntimeError` ②`content=None` ③`content=""` ④`content` 501 字 ⑤正常 20 字带引号 | ①②③④ 返回原问题且不抛异常；⑤ 去掉首尾空白与中英文引号 | 无 | patch `aurora.rewrite.get_client` | P0 | Planned |

| RWT-UNIT-004 | rewrite | Unit | "只有说话人标签"的历史 | `history=[{"role":"x","content":None}]` → `_format_history` 产出 `"Aurora："`（非空字符串、无正文） | 现状：字符串非空 → **仍会调用模型**。需先定义"有效历史文本"= 至少一条正文非空，否则直接返回原问题 | 无 | fake + 调用计数 | P2 | Planned |

| RWT-INT-001 | rewrite→pipeline | Integration | 改写只污染查询侧 | 伪造 standalone 与原问题不同 | `route` 与 `ask_rag(query=)` 收到 standalone；`run_agent` 的 question、`ask_rag(question=)`、`add_turn` 收到原话 | pipeline | patch `route/run_agent/ask_rag`，捕获实参 | P0 | Planned |

| RWT-AI-001 | rewrite | AI Eval | 指代消解与数值保真 | 数据集：代词、省略、参数替换、"换话题" | 报告 exact/semantic accuracy、数值与单位保真率、错误改写率；失败样本人工复核 | 真实 LLM | 不 Mock，独立付费作业 | P1 | Planned |

### 6.3 Router `router.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| RTR-UNIT-001 | router | Unit | 合法结构化输出 | tool_call 参数 `{"circuit_type":"opamp","confidence":0.9,"reason":"求 uo"}` | 返回三字段；`confidence` 为 `float`；`reason` 原样 | 无 | patch `aurora.router.get_client` | P0 | Planned |

| RTR-UNIT-002 | router | Unit | 无 tool_call 兜底 | `message.tool_calls=[]` 或 `None` | `{"circuit_type":"concept","confidence":0.0,"reason":"Router 未返回有效分类"}` | 无 | fake | P0 | Planned |

| RTR-UNIT-003 | router | Unit | 坏 JSON 兜底 | `arguments="{oops"` | `concept / 0.0 / "分类参数解析失败"` | 无 | fake | P0 | Planned |

| RTR-UNIT-004 | router | Unit | 非法枚举 | `circuit_type="unknown"`；缺失该字段 | 均回退 `concept`；缺失时 `reason` 仍保留 | 无 | fake（两份响应参数化） | P0 | Planned |

| RTR-UNIT-005 | router | Unit | 非 dict JSON 暴露类型契约 | `arguments="[]"` / `"\"opamp\""` / `"null"` | 现状：`args.get` 抛 `AttributeError`，Router 不兜底 → 中断 Pipeline。目标：与其他坏输出一致回退 | 无 | fake | P0 | Planned |

| RTR-UNIT-006 | router | Unit | `confidence` 类型契约 | `"high"` / `null` / `2.5` / `-1` / 缺失 | 现状：`"high"`→`ValueError`；`null`→`TypeError`；`2.5`/`-1` 不钳位；缺失→`0.0`。修复后应统一为"非数值回退 0.0 + 钳位到 [0,1]" | 无 | fake（5 份响应参数化） | P0 | Planned |

| RTR-UNIT-007 | router | Unit | 请求参数契约 | 任意问题 | `tool_choice` 强制 `route_circuit`；`temperature=0.0`；`tools=[ROUTE_TOOL]`；system 消息含五类说明 | 无 | fake 记录 kwargs | P2 | Planned |

| RTR-INT-001 | router→pipeline | Integration | 分类结果驱动分支 | 五类固定 Router 输出 | 只调用对应能力：`opamp`→`run_agent`；`feedback/power`→`ask_rag` 且 `system_prompt` 分别为 `FEEDBACK_PROMPT`/`POWER_PROMPT`；`concept`→`system_prompt is None`；`fallback`→不调用任何能力 | pipeline | patch `aurora.pipeline.route` 与三个能力函数 | P0 | Planned |

| RTR-E2E-001 | router | E2E | 分类→分支→返回字段一致 | 五类问题各一（fake LLM 按脚本回复） | 返回 dict 七字段齐全；`circuit_type` 与分支产物匹配（`trace`/`hits` 互斥且正确） | 全部 | 离线 fake | P0 | Planned |

| RTR-AI-001 | router | AI Eval | 五类分类质量与混淆 | 分层标注集（同义改写/边界/超纲），≥150 题 | 报告 accuracy、macro-F1、每类 recall、混淆矩阵、置信度校准（ECE） | 真实 DeepSeek | 不 Mock；≥3 次采样取多数 | P0 | Planned |

| RTR-AI-002 | router | AI Eval | 标签定义一致性 | 人工构造"跨类"问题（如"负反馈对运放输出有什么影响"） | 明确该归 `feedback` 还是 `opamp`，形成判定规范；统计"规范歧义题"占比并单列 | 标注规范 | 双人标注 | P2 | Planned |

### 6.4 知识入库数据层 `chunker.py` / `card_loader.py` / `embedder.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| CHK-UNIT-001 | chunker | Unit | 私有区与全角字符清洗 | `"输出＝１５Ｖ，正常"`、含 PUA 码点行、含 `\|` 表格行 | 实测：清洗为 `"输出正常"`（全角 `＝１５Ｖ` 因 ≥U+E000 被删，**已知数据损失**）；`\|` 变空格；纯符号行被丢弃 | 无 | 无 | P0 | Planned |

| CHK-UNIT-002 | chunker | Unit | 可读性与占位过滤 | 纯英文行、纯公式行、含"请替换文字"的行、含任意一个汉字的行 | 前 3 类被丢弃；含汉字行保留 | 无 | 无 | P0 | Planned |

| CHK-UNIT-003 | chunker | Unit | 标题识别与继承 | `"1 集成运算放大器"`、`"１.2 标题"`（全角数字）、`"2026 年考试"` | ASCII 数字开头且 ≤30 字 → 成为 heading 且不进正文；全角数字被清洗成 `".2 标题"` → **不识别为标题**（实测 heading 为 `""`）；误判为标题的行不进正文 | 无 | 无 | P1 | Planned |

| CHK-UNIT-004 | chunker | Unit | 600 字封口与残段 | 多段正文累计跨过阈值；末尾不足阈值 | 达到阈值即 `flush`；末段 `flush`；chunk 的 `source` 为文件名、`heading` 为最近标题；注意阈值按"无空格拼接长度"计算，实际 text 略长 | 无 | 无 | P1 | Planned |

| CHK-UNIT-005 | chunker | Unit | 编码与异常 | 非 UTF-8 文件；不存在的路径 | `chunk_file` 抛 `UnicodeDecodeError` / `FileNotFoundError`，不静默返回空列表 | 文件系统 | `tmp_path` | P2 | Planned |

| CRD-UNIT-001 | card_loader | Unit | 多卡解析 | 含前言、3 张 `##` 卡、字段行、空行的 md | 3 个 `Chunk`；`text` 以"标题。"开头并拼接字段；`source="公式卡片：<标题>"`；`heading=标题` | 文件系统 | `tmp_path` 写临时 md | P0 | Planned |

| CRD-UNIT-002 | card_loader | Unit | 空卡与异常结构 | 只有标题无正文；连续两个 `##`；`#` 一级标题 | 空卡仍生成 Chunk（text 仅标题）；一级标题不参与解析；不抛异常 | 文件系统 | `tmp_path` | P1 | Planned |

| EMB-UNIT-001 | embedder | Unit | 查询/文档调用契约 | 一批 passages + 单条 query | passages **不加**前缀、`normalize_embeddings=True`、`show_progress_bar=False`、`convert_to_numpy=True`；query **加** `QUERY_INSTRUCTION`；两者 `dtype=float32` | sentence-transformers | patch `aurora.embedder.get_model` 为记录入参的假模型 | P0 | Planned |

| EMB-UNIT-002 | embedder | Unit | 形状与缓存 | 3 条文本；连续两次 `get_model()` | 返回 `(3, D)`；`get_model` 走 `lru_cache` 只加载一次；用例前后 `cache_clear()` | 无 | 同上 | P1 | Planned |

### 6.5 向量库 `vector_store.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| VST-UNIT-001 | vector_store | Unit | top-k 排序正确 | 3 个二维单位向量 + 查询 `[1,0]` | 分数降序；`score` 与手算点积一致；返回 `SearchResult` | NumPy | 无 | P0 | Planned |

| VST-UNIT-002 | vector_store | Unit | k 的边界 | `k=1`、`k=3`、`k=5`（>N）、`k=0`、`k=-1` | `1/3/3/[]`；实测 `k=-1` 返回 `[第1,第3]`（切片 `[:-1]` 丢最低分）→ 作为当前语义固定，修复时改显式校验 | NumPy | 无 | P0 | Planned |

| VST-UNIT-003 | vector_store | Unit | 空库与 `None` 向量 | `VectorStore()`；`vectors=None` | 返回 `[]`，不抛异常 | NumPy | 无 | P0 | Planned |

| VST-UNIT-004 | vector_store | Unit | 形状/数量不一致 | query 维度 ≠ 向量维度；`len(chunks) != len(vectors)` | 前者 `ValueError`；后者当前**不报错**，按位置取会导致错配 → 记录为契约缺陷 | NumPy | 无 | P1 | Planned |

| VST-INT-001 | vector_store | Integration | 保存/加载往返 | 2 个 Chunk + 固定矩阵 → `tmp_path` → `load` | 文本、`source`、`heading`、顺序、dtype、检索结果全等；不触碰 `data/index/` | 文件系统 | `tmp_path` | P0 | Planned |

| VST-INT-002 | vector_store | Integration | 坏持久化输入 | 缺 `vectors.npy`；`chunks.json` 非法；两者数量不匹配 | 前两者抛 `FileNotFoundError` / `JSONDecodeError`；第三者当前可加载成功 → 需在加载时校验 | 文件系统 | `tmp_path` | P1 | Planned |

| VST-INT-003 | vector_store | Integration | `add()` 契约 | 空库 `add` 两次；有 `vectors=None` 时 `add` | 向量 `vstack` 后形状 `(2,D)`；`len(store)==2`；注意 `add` 目前**无调用方**，仅锁定行为 | NumPy | 无 | P2 | Planned |

### 6.6 RAG 在线链 `rag.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| RAG-UNIT-001 | rag | Unit | 上下文拼接格式 | 2 个 `SearchResult`（source A/B） | 编号从 1 起、含"（来源：X）"、顺序与 hits 一致、正文完整无截断 | 无 | 无 | P0 | Planned |

| RAG-UNIT-002 | rag | Unit | 空 hits 文案 | `build_context([])` | 返回 `""`；`ask_rag` 在空 hits 时返回"课件库还是空的…"且**不调用** `get_client` | 无 | 哨兵 client | P0 | Planned |

| RAG-INT-001 | rag | Integration | query 与生成输入隔离 | `question="那 R1 换成 20k 呢"`、`query="反相比例电路 R1=20k 求 uo"`、含 history | `encode_query` 收到 query；user 消息含原问题与全部片段；history 顺序为 system→history→user；history 未被原地修改 | VectorStore 真、Embedding/LLM 假 | patch `aurora.rag.encode_query` 与 `get_client` | P0 | Planned |

| RAG-INT-002 | rag | Integration | 自定义提示词生效 | `system_prompt="X"` / `None` | 分别使用 `"X"` 与 `RAG_SYSTEM_PROMPT`；`temperature=0.3` | 同上 | 同上 | P0 | Planned |

| RAG-INT-003 | rag | Integration | top_k 透传 | `top_k=1` / `5` / `0` | 传入 `store.search` 的 k 与返回值长度一致；`k=0` → 空 hits → 走"课件库还是空的"分支 | VectorStore | 假 encoder 返回固定向量 | P1 | Planned |

| RAG-INT-004 | rag→pipeline | Integration | `store=None` 的真实失败面 | Router 判 `concept`，`run(question, None)` | 现状：`retrieve(None, ...)` → `None.search` → `AttributeError`，Session 不写回。属**已知契约风险**，需决策后固定 | pipeline | patch Router，其余真实 | P0 | Planned |

| RAG-INT-005 | rag→session | Integration | `content=None` 污染多轮状态 | fake 生成响应 `content=None` | 现状：`answer=None` 被 `add_turn` 写入，`len(session)==2` 且第二条 `content is None`。目标：抛明确异常且长度不变 | pipeline、session | patch Router + `aurora.rag.get_client` | P0 | Planned |

| RAG-INT-006 | rag | Integration | 检索分数非归一化风险 | 未归一化向量入库 | 点积不再等于余弦；记录该前置条件必须在 `embedder` 侧保证 | NumPy | 无 | P2 | Planned |

| RAG-AI-001 | retrieval | AI Eval | 检索质量 | 标注 `query → 相关 chunk/source`，含"无答案"题 | 报告 Recall@1/3/5、MRR、nDCG@5；"无答案误检率"单列；固定索引版本与 hash | 真实 BGE + 冻结索引 | 不 Mock | P0 | Planned |

| RAG-AI-002 | generation | AI Eval | 正确性/忠实度/引用 | concept/feedback/power 代表题 + 片段不足的对抗题 | 报告答案正确率、faithfulness、诚实拒答率、citation precision/recall | 真实 LLM | 不 Mock；规则校验 + 人工抽检 | P0 | Planned |

| RAG-AI-003 | ingestion | AI Eval | 切块策略的影响 | 固定语料，对比 600 字 vs 其他阈值、有无卡片 | 记录 Recall/MRR 差异；单次波动不作为回归判据 | 真实 Embedding | 离线实验，产物隔离 | P2 | Planned |

### 6.7 工具层 `tools.py`（安全边界，P0 最密集）

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| TOL-UNIT-001 | tools | Unit | 白名单正常计算 | `-(100/10)*(-3)`、`3**4+sqrt(2)*sqrt(8)`、`pi`、`e`、`-2`、`7%3` | 分别得 `30.0`、`97.0`、`π`、`e`、`-2`、`1`；返回 `float` | 无 | 无 | P0 | Planned |

| TOL-UNIT-002 | tools | Unit | 拒绝危险语法 | `__import__("os").system(...)`、`(1).__class__`、`[1,2][0]`、`lambda:1`、`a=1`、字符串拼接 | `a=1` 在 `ast.parse(..., mode="eval")` 阶段抛原生 `SyntaxError`；其余可成功解析但不在 AST 白名单中的表达式抛 `ValueError`；全部被拒且无副作用 | 无 | 无 | P0 | Planned |

| TOL-UNIT-003 | tools | Unit | 关键字参数不得静默改写语义（DEF-01） | `round(3.14159, ndigits=2)`；`round(3.14159, 2)` | 前者返回 `计算失败：不支持关键字参数…`（显式拒绝），后者返回 `"3.14"`；不允许再出现静默丢弃后返回 `"3"` | 无 | 无 | P0 | Existing / Not Executed |

| TOL-UNIT-004 | tools | Unit | 精度截断 | `123456789`、`100/3`、`2**40` | 实测 `"1.23457e+08"`、`"33.3333"`、`"1.09951e+12"`（`:g` 仅 6 位有效数字）→ 测死当前行为，修复后应保留足够精度 | 无 | 无 | P1 | Planned |

| TOL-UNIT-005 | tools | Unit | 异常转换矩阵 | `1/0`、`log(0)`、`sqrt(-1)`、`10**10000` | 全部返回 `"计算失败：…"` 字符串（实测通过） | 无 | 无 | P0 | Planned |

| TOL-UNIT-006 | tools | Unit | 预期表达式错误转换（DEF-02） | `(-8)**(1/3)`（复数结果）；`"1+"`（`SyntaxError`）；`sqrt()`（参数个数不符的 `TypeError`） | `calculate_expression()` 对非实数抛 `ValueError("计算结果不是实数")` 并保留 `ast.parse()` 原生 `SyntaxError`；`execute_tool()` 分别转为 `计算失败：计算结果不是实数`、`计算失败：表达式语法错误`、`计算失败：参数不匹配…`，均不向 `run_agent()` 逃逸 | 无 | 无 | P0 | Existing / Not Executed |

| TOL-UNIT-007 | tools | Unit | 分派与参数校验 | 未知工具名；`arguments` 非 dict；`expr` 缺失/空串/非字符串 | 返回可识别错误字符串，不抛异常 | 无 | 无 | P0 | Planned |

| TOL-UNIT-008 | tools | Unit | 布尔字面量类型放宽 | `"True"` / `"False"` | 实测返回 `"1"` / `"0"`（`bool` 是 `int` 子类）→ 明确是否收紧为拒绝 | 无 | 无 | P2 | Planned |

| TOL-UNIT-009 | tools | Unit | 运放校验边界与符号 | `15`、`-15`、`15.01`、`-20`、`"错误：…"`、`"abc"`、`supply=12` | 边界值不告警；越界告警且符号 `+/-` 正确；非数值返回 `None`；`supply` 参数生效 | 无 | 无 | P0 | Planned |

| TOL-UNIT-010 | tools | Unit | 资源边界（隔离执行） | `10**10000000`、超长表达式、深 AST | 在**子进程 + 内存/CPU 限制**中运行，断言不拖垮主进程；标 `slow`，不进默认 CI | OS | 子进程 | P2 | Planned |

| TOL-AI-001 | tools/agent | AI Eval | 工具调用准确率 | 需算/无需算、单步/多步、单位干扰、非法表达式 | 报告"是否调用工具"决策准确率、工具名与参数 exact match、执行成功率、最终数值正确率 | 真实 LLM + 本地工具 | 不 Mock，独立作业 | P0 | Planned |

### 6.8 Agent 回环 `agent.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| AGT-UNIT-001 | agent | Unit | 单轮工具 + 终答 | 第 1 次返回 1 个 tool_call，第 2 次返回 `content` | 返回 `(content, trace)`；messages 顺序 `system→user→assistant(tool_calls)→tool`；trace 长度 1 | 无 | scripted fake | P0 | Planned |

| AGT-UNIT-002 | agent | Unit | 一次多个 tool_calls | 单次返回 2 个 tool_call | 生成 2 条 `tool` 消息且 `tool_call_id` 各自对应；trace 顺序与调用一致 | 无 | scripted fake | P0 | Planned |

| AGT-UNIT-003 | agent | Unit | 坏参数与未知工具 | `arguments="{oops"`；`name="unknown_tool"` | 坏 JSON 变 `{}` → 工具返回"缺少字符串参数"；未知工具返回"未知工具"字符串；均作为 `tool` 消息回传，不抛异常 | tools | scripted fake | P0 | Planned |

| AGT-UNIT-004 | agent | Unit | 终止与异常 | ①连续 5 轮只返回 tool_call ②`finish_reason="length"` 且无内容 ③无 tool_call 且 `content=None` 且 `finish_reason="stop"` | ① 返回占位文案 + 完整 trace（**当前被当成功**）② `RuntimeError("模型输出达到长度限制")` ③ `RuntimeError("模型没有返回有效内容")` | 无 | scripted fake | P0 | Planned |

| AGT-UNIT-005 | agent | Unit | validator 挂载与消息拼装 | `validators={"calculate_expression": f}`，f 返回 `"WARN"` | `tool` 消息内容为 `"<result>\n[合理性校验] WARN"`；trace 同时含 `result` 与 `warning`；无 validator 时内容仅为 `result` | 无 | scripted fake + 直接调用 | P0 | Planned |

| AGT-UNIT-006 | agent | Unit | history 只接受纯文本 | `history` 含 `tool` 角色消息 | 现状：原样 `extend` 进 messages，不做过滤 → 记录为契约风险（应由调用方保证） | 无 | scripted fake 记录 messages | P2 | Planned |

| AGT-INT-001 | agent→tools | Integration | 真实执行 + 校验回传 | 假 LLM 先请求 `-(100/10)*(-3)`，再出终答 | trace: `result="30"`、`warning` 含"钳位在 +15V"；第 2 次请求的末条 `tool` 消息含该警告 | 真实 tools | 只 patch `aurora.agent.get_client` | P0 | Planned |

| AGT-INT-002 | agent→tools | Integration | 中间量误触发电压校验 | 先 `100000*3`，再 `0.5*10` | 现状：**每次** `calculate_expression` 都跑 validator，第 1 步就产生"饱和"警告 → 需决策"只校验最终输出"或"取消按工具名自动校验" | 真实 tools | scripted fake 捕获每轮消息 | P0 | Planned |

| AGT-INT-003 | agent→tools | Integration | 工具异常不外溢 | 假 LLM 请求 `(-8)**(1/3)` | 历史状态：`TypeError` 曾逃出 `run_agent` 并中断 Pipeline；当前预期：非实数错误作为 `tool` 消息和 trace 回传，Agent 继续下一轮并返回最终回答 | 真实 tools | scripted fake | P0 | Planned |

### 6.9 Pipeline 编排与端到端

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| PIP-UNIT-001 | pipeline | Unit | 降级文案 | `fallback_answer("用 Python 写快排")` | 含原问题、能力清单、学习路径；纯函数无副作用 | 无 | 无 | P0 | Planned |

| PIP-UNIT-002 | pipeline | Unit | 提示词分派表 | `RAG_PROMPTS` 的键集合 | 恰为 `{feedback, power}`；`concept` 不在表中（回落到默认提示词）；`OPAMP_VALIDATORS` 只挂 `calculate_expression` | 无 | 无 | P1 | Planned |

| PIP-INT-001 | pipeline | Integration | 五分支统一返回 | 参数化五类 + `top_k=5` + 有 Session | 七字段齐全且类型正确；`trace`/`hits` 互斥；`session` 恰好 +2 且为原话 + 最终答案 | 下游替身 | patch `route` 与三个能力函数 | P0 | Planned |

| PIP-INT-002 | pipeline | Integration | 下游异常传播且不写回 | ①`route` 抛错 ②`run_agent` 抛错 ③`ask_rag` 抛错 ④`rewrite` 抛错 | ①②③ 异常向上传播且 `len(session)==0`；④ 回退原问题并继续执行 | 下游替身 | 参数化 patch | P0 | Planned |

| PIP-INT-003 | pipeline | Integration | fallback 零外部调用 | Router 固定 `fallback` | `run_agent`/`ask_rag` **未被调用**（哨兵）；`trace=hits=None` | 无 | 哨兵替身 | P0 | Planned |

| PIP-INT-004 | pipeline | Integration | 临时 Session 无跨调用泄漏 | 连续两次 `run(q, store)` 不传 session | 两次的 `history` 均为 `[]`；无全局可变状态 | pipeline | patch 能力函数捕获 `history` | P0 | Planned |

| PIP-INT-005 | pipeline | Integration | 轮数耗尽被当成功 | opamp 分支 fake LLM 5 轮都只调工具 | 现状：占位文案写入 Session，`answer` 与正常答案无法区分 → 目标：抛明确异常或返回可识别失败标记，Session 不变 | agent、session | scripted fake | P0 | Planned |

| PIP-E2E-001 | pipeline | E2E | 离线五类冒烟 | 5 个代表问题 + 3 个二维向量的小索引 + scripted LLM | 每类命中正确分支；返回 schema 与 `demo_*.py` 的消费方式兼容；Session 各 +2 | 全链 | 统一 fake，不联网 | P0 | Planned |

| PIP-E2E-002 | pipeline | E2E | 空索引全链 | 空 `VectorStore` + concept 路由 | 返回"课件库还是空的…"，`hits==[]`，Session +2（**该降级文案会进历史**，需决策） | 全链 | fake | P1 | Planned |

| PIP-E2E-003 | pipeline | E2E | 在线冒烟（少量） | 五类各 1 题 + 1 个多轮剧本 | 请求成功、schema 完整、无未处理协议错误；**不判质量** | DeepSeek、BGE、真实索引 | 不 Mock，手动/定时 | P1 | Existing scripts / Not Executed |

### 6.10 Session 与多轮

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| SES-UNIT-001 | session | Unit | 创建、顺序、隔离 | 新建两个 Session，各 `add_turn` | 默认 `[]`；顺序 `user,assistant`；`len()` 正确；两实例互不影响 | 无 | 无 | P0 | Planned |

| SES-UNIT-002 | session | Unit | 公开可变引用语义 | `ctx = s.context(); ctx.append(...)`；改写 `ctx[0]["content"]` | 现状：**直接改变 Session 内部状态** → 测死该风险语义，不写成"隔离保证" | 无 | 无 | P1 | Planned |

| SES-UNIT-003 | session | Unit | 空/异形输入 | `add_turn("", "")`；`add_turn(None, None)` | 现状：照单写入，不做校验 → 记录契约边界 | 无 | 无 | P2 | Planned |

| SES-INT-001 | multi-turn | Integration | 上下文传递 + 原话写回 | 两轮复用同一 Session，第二轮为省略追问 | 第二轮 `rewrite` 收到 2 条历史；生成侧 history 为纯文本；最终 4 条消息且第 3 条是**原追问**而非 standalone | pipeline | scripted fake | P0 | Planned |

| SES-INT-002 | multi-turn | Integration | 换话题不被拽回 | 先 opamp 再问 feedback | 第 2 轮 Router 收 standalone；走 RAG 分支；历史顺序不变 | pipeline | scripted fake | P1 | Planned |

| SES-INT-003 | multi-turn | Integration | 失败轮次不产生半条历史 | 第 1 轮成功、第 2 轮 `ask_rag` 抛错 | 第 2 轮后 `len(session)` 仍为 2，历史无半轮 | pipeline、session | patch 能力函数 | P0 | Planned |

| SES-INT-004 | multi-turn | Integration | 长历史无窗口 | 连续 10 轮 | 现状：`context()` 返回全部 20 条，无截断 → 记录 token 增长风险与后续窗口需求 | pipeline | fake | P2 | Planned |

| SES-E2E-001 | multi-turn | E2E | 三轮闭环 | 与 `demo_multi_turn.py` 同剧本 | `0→2→4→6`；第 2 轮参数保真；第 3 轮类型切换为 `feedback` | 全链 | scripted fake，真实 Session | P0 | Existing demo assertion / Not Executed |

| SES-RISK-001 | session | Integration | 并发非安全观测 | 2 线程共享一个 Session 各写 50 轮 | 不设"通过"门槛；若观察到条数 <100 或消息错序即记录；标 `slow`，不进 CI | 线程调度 | 受控实验 | P2 | N/A for current contract |

| MTR-AI-001 | multi-turn | AI Eval | 上下文理解质量 | 指代/参数替换/纠错/换题/长历史剧本 | standalone 保真率、路由准确率、答案是否用新参数、旧数值污染率 | 真实 LLM | 不 Mock；逐轮存档 | P1 | Planned |

### 6.11 Judge 与评测脚本

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| JUD-UNIT-001 | judge | Unit | 无 tool_call / 坏 JSON | 两份异常响应 | `judge_answer` 默认 `partial`；`judge_hallucination` 默认 `False`；reason 可识别 | 无 | patch `aurora.judge.get_client` | P0 | Planned |

| JUD-UNIT-002 | judge | Unit | 非法枚举/类型收敛 | `judgment="maybe"`；`hallucinated="yes"`；`hallucinated=1` | 非法枚举 → `partial`；非 `bool` → `False` | 无 | fake | P0 | Planned |

| JUD-UNIT-003 | judge | Unit | 字段回填契约 | 正常 `{"judgment":"correct","reason":"…"}` | 返回 `{"judgment": "correct", "reason": "…"}`；幻觉分支返回键为 `hallucinated` 而非 `judgment` | 无 | fake | P1 | Planned |

| EVL-UNIT-001 | eval.py | Unit | 关键词判分边界 | `expected="5V\|5 V"`；答案含"不是 5V"；`answer=None` | 现状：任一子串命中即 True（含否定句假阳性）；`None` 不抛异常返回 False | 无 | 直接导入 `scripts.eval.is_correct` | P1 | Planned |

| EVL-UNIT-002 | eval_report.py | Unit | 引用率/多数投票 | 重复 source、空 hits、投票 2:1 与 1:1 | 命中率 = 命中来源数/片段数（重复来源会重复计数）；`hits=[]` → `None`；平票取先出现值 | 无 | 无 | P1 | Planned |

| EVL-UNIT-003 | eval_report.py | Unit | 汇总分母 | 12 题中 7 道有 hits | 引用率与幻觉率分母为 RAG 题数（7），不是总题数；无 RAG 题时打印"无 RAG 题"且不除零 | 无 | 构造 rows | P1 | Planned |

| EVL-META-001 | judge | AI Eval（元） | 裁判与人工一致性 | 人工双标 40 条（正确/部分/错误/幻觉） | 报告 agreement、Cohen's κ、各类 precision/recall；同源模型偏差单列 | 裁判 LLM + 人审 | 盲审 + 异源裁判抽检 | P0 | Planned |

### 6.12 建库脚本与旧入口

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority | Status |

|---|---|---|---|---|---|---|---|---|---|

| IDX-INT-001 | build_index | Integration | 可控语料建库 | `tmp_path` 下 2 个 md + 1 个卡片文件 + 假 encoder | Chunk 总数 = 课件块 + 卡片块；落盘两个文件；自检 `top[0].score ≈ 1`；**不写真实索引** | 文件系统 | patch 路径常量 + `encode_passages` | P1 | Planned |

| IDX-INT-002 | build_index | Integration | 空语料与缺失卡片 | courseware 目录为空；卡片文件不存在 | 空目录 → 打印提示并 `return 1`；缺卡片 → 跳过并继续 | 文件系统 | `tmp_path` | P1 | Planned |

| LGC-UNIT-001 | companion_v0 | Unit | 消息构造 | `ask("q", history=[...])` | messages 为 `system→history→user`；`temperature=0.7` | 无 | patch `aurora.companion_v0.get_client` | P2 | Planned |

| LGC-E2E-001 | chat_loop | E2E | 旧入口冒烟 | 单轮输入 + `exit` | 能正常退出；不涉及 Pipeline（明确记录"两条入口"现状） | 真实 LLM | 自动化需 patch `builtins.input` 与 `ask_stream`；当前仅有手工入口 | P2 | Existing / Not Executed |

---

## 7. 外部依赖与 Mock 策略汇总

| 依赖 | 出现在 | 默认测试策略 | 打补丁位置 | 注意事项 |

|---|---|---|---|---|

| DeepSeek Chat | router / rewrite / rag / agent / judge / companion_v0 | 一律 fake | `aurora.<module>.get_client` | 必须 patch 使用方，不能只 patch `config` |

| Embedding（BGE） | embedder / rag | 假 encoder 或固定向量 | `aurora.embedder.get_model`、`aurora.rag.encode_query` | `lru_cache` 需 `cache_clear()`；首载约 100MB / 分钟级 |

| 向量库 | rag / pipeline | 内存 `VectorStore` + 手算二维向量 | 无 | 不要依赖 `data/index/`（被忽略、可重建） |

| 文件系统 | chunker / card_loader / vector_store / build_index | `tmp_path` | 显式传目录 | 禁止写 `data/`、`.env`、`../courseware_md/` |

| 环境变量 | config | patch 模块常量 | `aurora.config.DEEPSEEK_API_KEY` | 模块导入即冻结，`setenv` 无效 |

| 网络 | 全部 | autouse 阻断 | `socket.socket.connect` | 漏网请求立即失败，避免费用 |

| 时间/随机 | eval_report 延迟 | 不打桩，只做冒烟 | — | 延迟不进断言阈值 |

**统一 fake 设计**（写在 `tests/conftest.py`）：

```python

# 伪代码意图：响应按调用顺序弹出，队列空即断言失败

class ScriptedClient:

    def __init__(self, responses): self._q = deque(responses); self.calls = []

    @property

    def chat(self): return self

    @property

    def completions(self): return self

    def create(self, **kwargs):

        self.calls.append(kwargs)

        if not self._q: raise AssertionError("意外的额外模型调用")

        return self._q.popleft()

```

三个必备断言能力：**调用次数**（多调/少调都失败）、**调用参数**（`tools`/`tool_choice`/`temperature`/`messages`）、**消息顺序**。

---

## 8. 测试数据与 Fixture 设计

建议目录（尚未创建）：

```text

tests/

├── conftest.py                 # 断网、哨兵 client、fake client 工厂、message 构造器

├── fixtures/

│   ├── vectors.py              # 3~5 个可手算二维向量 + 对应 Chunk

│   ├── llm_responses.py        # Router/Rewrite/RAG/Agent/Judge 的正常与畸形响应

│   └── knowledge/              # 微型 md、公式卡片、全角/PUA/占位样本

├── unit/                       # test_tools.py / test_chunker.py / ...

├── integration/                # test_pipeline_branches.py / test_agent_tools.py

├── e2e/                        # test_pipeline_offline.py / test_multi_turn.py

└── online/                     # marker: online / ai_eval / slow

```

数据设计要点：

1. **微型向量集**：`[[1,0],[0,1],[0.6,0.8],[0,1]]` 可覆盖"降序""同分""k>N""空库""负 k"，且分数可用手算核对。

2. **微型知识库**：3 个 Chunk（含 1 个最高分、1 个无关），断言检索排序与 `build_context` 编号，不加载真实索引。

3. **畸形响应矩阵**：`tool_calls=[]`、`arguments="{oops"`、`arguments="[]"`、`arguments="null"`、`confidence="high"`、`confidence=null`、`content=None`、`finish_reason="length"`。**这 8 种是 Router/Agent 崩溃的高发点**，应集中在一个参数化用例里。

4. **文本边界样本**：全角数字标题、全角单位、PUA 码点、表格竖线、占位文案、纯公式行。

5. **AI Eval 黄金集**（独立于 `tests/`）：每条至少含 `id / capability / difficulty / expected_type / gold_sources / acceptable_answers / forbidden_facts / turn_relation / reviewer`。当前 12 题只能当 smoke seed，不能当泛化集。

6. **数据切分**：同一知识点的同义改写不得同时进开发集与报告集，否则指标虚高。

---

## 9. 现有测试审计结果

### 9.1 数量（已核实）

| 项目 | 实际情况 |

|---|---|

| `tests/` | **不存在** |

| `evals/` | **不存在** |

| pytest / unittest 用例 | **0** |

| 测试依赖 | 审计时 `pyproject.toml` 与 `.venv` 均无 `pytest`；当前已声明、锁定并安装 `pytest 9.2.0` |

| CI / lint / type-check | 无配置（无 `.github/`、无 ruff/mypy） |

| 可执行的运行时断言 | **1 处**：`scripts/demo_multi_turn.py` 末尾 `assert len(session) == 6` |

| 评测数据 | `data/eval_set.json` 12 条 |

| 评测脚本 | 2 个（`eval.py`、`eval_report.py`），均需真实 API + BGE + 已建索引 |

| 本次实际运行 | **0 个用例**；只做了 §14 列出的离线探针 |

### 9.2 已有脚本的真实覆盖与局限

- `eval.py`：跑完整 Pipeline 的在线 happy path；答案判分是"任一关键词子串命中"，**否定句也会被判对**（如期望 `5V`、回答"不是 5V" → 判对）。

- `eval_report.py`：多次采样 + 多数投票 + LLM judge + 幻觉 + 引用命中率 + 延迟；但考生与裁判同为 DeepSeek（同源偏差），报告只打印到终端，**不保存模型版本/索引 hash/token/费用**，无历史基线。

- `demo_multi_turn.py`：只断言"历史长度 6"，**不能**证明改写正确、分支正确、答案用了新参数。

- `build_index.py` 的自检不断言分数，分数低也不影响退出码。

- 两个评测脚本强依赖网络与本地模型，**不适合默认 CI**。

### 9.3 文档与代码的差异（以代码为准）

| 文档声明 | 代码实际 |

|---|---|

| README "只支持单轮，`pipeline.run()` 不接收 history" | 已支持 keyword-only `session` + `rewrite_question` + 历史透传 |

| README 目录清单 | 缺 `rewrite.py`、`session.py`、`judge.py`、`demo_multi_turn.py`、`eval_report.py` |

| README "12/12" | 是开发时记录，无产物、非本次运行结果 |

| `AGENTS.md` "只在成功得到答案后写回" | `content=None` 与轮数耗尽占位文案**都会被写回** |

| `codebase-overview.md` "Router 坏 JSON 会回退" | 仅 JSON 语法错误回退；非 dict JSON → `AttributeError`，非数值 confidence → `ValueError`/`TypeError` |

| **历史审计状态**：`codebase-overview.md` 未提工具 `TypeError` 逃逸 | `(-8)**(1/3)` 当时会让 opamp 链路崩溃；当前已由计算层拒绝非实数并在工具边界转换为错误字符串 |

---

## 10. 测试覆盖缺口（按风险排序）

| # | 缺口 | 后果 | 证据 | 优先级 |

|---|---|---|---|---|

| 1 | **历史风险（已整改）**：工具层 `TypeError`/`SyntaxError` 逃逸 | 历史行为会因非实数或残缺表达式中断 opamp 链路；当前预期错误已转换为模型可读字符串 | 历史 `tools.py:132-136`；离线实测；当前由 TOL-UNIT-006、AGT-INT-003 覆盖 | P0 |

| 2 | RAG `content=None`、Agent 轮数耗尽被当成功 | `None` 或占位文案写进历史，下一轮请求可能直接失败或语义污染 | `rag.py:93`、`agent.py:130`、`pipeline.py:155` | P0 |

| 3 | Router 非 dict JSON / 非数值 confidence 不兜底 | 第三方响应形状一变即 `AttributeError`/`TypeError` | `router.py:119-125` | P0 |

| 4 | validator 对每个中间计算都生效 | 中间量 >15 即误报"运放饱和"，诱导模型改错结论 | `agent.py:107-109` | P0 |

| 5 | 五分支与失败写回无回归 | 分支错配、半轮历史污染不可见 | 全仓 0 用例 | P0 |

| 6 | `execute_tool` 关键字参数被静默丢弃（已整改为显式拒绝）+ 6 位精度未处理 | 整改前：`round(x, ndigits=2)` 返回错值且**无任何告警**，而提示词要求模型“直接引用工具结果”→ 错值被当标准答案写进回答；精度问题仍在 | 整改前实测 `"3"`；遗留 `"1.23457e+08"` | P0（已整改，待复验）/ P1（精度） |

| 7 | 向量与 chunk 数量/维度无校验 | 索引损坏时错配命中或深层异常 | `vector_store.py:52-67` | P1 |

| 8 | 无相似度阈值、无"无答案"标注 | 无关问题也硬塞片段，幻觉入口 | `rag.py:72` | P0（AI Eval） |

| 9 | 评测集仅 12 题且模板化 | 指标不代表泛化，`12/12` 无统计意义 | `data/eval_set.json` | P0 |

| 10 | Session 返回内部可变列表 + 无限增长 | 状态可被绕过写接口修改；长会话 token 与费用线性增长 | `session.py:46-54` | P1 |

| 11 | 超时/重试依赖 SDK 默认 | 已实测 `read=600s`、`max_retries=2`，非项目显式契约 | `config.py:30` | P1 |

| 12 | 索引不可复现 | 课件在仓库外、`data/index` 被忽略，他机无法重建同结果 | `.gitignore`、`build_index.py:14` | P1 |

| 13 | 评测不留档 | 无法做质量/成本/延迟回归 | `eval_report.py` 只 `print` | P1 |

| 14 | 全角字符被清洗 | `输出＝１５Ｖ` → `输出正常`，知识文本丢关键数值 | `chunker.py:46` + 实测 | P1（需重跑索引） |

---

## 11. 实施优先级与阶段安排

### 阶段 0：基础设施（0.5 天）

- 已完成：引入 `pytest` 为 dev 依赖并更新 `uv.lock`。这只完成依赖准备，不代表阶段 0 已通过。

- `tests/conftest.py`：三层断网防线（`config.OpenAI` 熔断 + socket 阻断 + `get_model` 替身）、`ScriptedClient` 工厂、`message/tool_call` 构造器。

- `tests/test_infra.py`：以下 8 条基础设施自检分别验证三层防线、微型数据、Fake 注入、业务模块绑定引用和测试凭据隔离：

| Test ID | 验证目标 | 关键断言 |
|---|---|---|
| `INF-UNIT-001` | OpenAI 客户端构造熔断 | dummy key 下 `get_client()` 命中构造哨兵，且未尝试网络 |
| `INF-UNIT-002` | Socket 网络熔断 | `socket.create_connection()` 在 `config.get_client()` 受控探针路径中被阻止 |
| `INF-UNIT-003` | BGE 模型加载熔断 | `encode_query()` 在模型构造或下载前失败 |
| `INF-UNIT-004` | 微型向量与合成知识 Fixture | 实际构造二维向量和符合 `Chunk` 字段契约的数据 |
| `INF-UNIT-005` | `ScriptedClient` 使用方注入 | Router 响应、调用次数、消息、model、temperature、tools 与 tool_choice 正确 |
| `INF-UNIT-006` | 业务模块 `get_client` 绑定引用熔断 | `router.get_client` 仍解析 `config.OpenAI` 哨兵，不能绕过全局防线 |
| `INF-UNIT-007` | 原生 Socket connect 熔断 | `socket.socket.connect()` 立即被阻止 |
| `INF-UNIT-008` | 测试凭据与 dotenv 隔离 | 默认环境和冻结配置只含 dummy key，且 `load_dotenv()` 拦截确实发生 |

- `pyproject.toml`：`[tool.pytest.ini_options]` 注册 `online / ai_eval / slow` marker，`addopts = -m "not online and not ai_eval and not slow"`。

- **准入**：`uv run pytest -q` 返回码 0 且上述 `INF-UNIT-001` 至 `INF-UNIT-008` 全部通过；`uv run pytest --collect-only -q` 能列出全部用例 ID，且无网络请求。

  注意：pytest 在**未收集到任何用例**时退出码为 5，因此"0 passed"不能作为通过条件。

### 阶段 1：纯逻辑 P0（1.5 天）

`tools`（TOL-UNIT-001/002/003/005/006/007/009）、`chunker`（CHK-UNIT-001/002）、`card_loader`（CRD-UNIT-001）、`vector_store`（VST-UNIT-001/002/003、VST-INT-001）、`session`（SES-UNIT-001）、`config`（CFG-UNIT-001）。

其中 TOL-UNIT-003 按 §4.4 用 `xfail(strict=True)` 定标"关键字参数应生效"，而不是断言当前的 `"3"`。

**准入**：上述用例全部 `passed` 或 `xfailed`（`xfail(strict=True)` 意外 `XPASS` 视为未通过）；不得用 `-k` / `--deselect` 排除已列入清单的用例；任何真实失败必须对应已登记缺陷，不允许"改测试迁就代码"。

### 阶段 2：结构化协议与失败路径（1.5 天）

`router`（RTR-UNIT-001~006）、`rewrite`（RWT-UNIT-001/003）、`judge`（JUD-UNIT-001/002）、`agent`（AGT-UNIT-001/003/004/005）。

**准入**：8 种畸形响应全部有确定行为；`TypeError`/`AttributeError` 的逃逸点有明确处理决策。

### 阶段 3：编排与多轮（2 天）

`pipeline`（PIP-INT-001~005）、`agent→tools`（AGT-INT-001/002/003）、`rag`（RAG-INT-001~005）、`session/multi-turn`（SES-INT-001/003）。

**准入**：五分支参数与返回全覆盖；失败轮次不写半条历史；`None`/占位文案的处置已定案。

### 阶段 4：离线 E2E（1 天）

`PIP-E2E-001/002`、`SES-E2E-001`、`RTR-E2E-001`。用 3~5 个手算向量 + scripted LLM 跑完整链。

**准入**：五类离线冒烟稳定、可重复、全程无网络；执行时间 < 10s。

### 阶段 5：AI Evaluation（2~3 天，独立作业）

1. 先做 `EVL-META-001`（裁判与人工对齐），**再**设质量门槛。

2. 扩集：`router` ≥150 题、`retrieval` ≥80 条标注、`generation` ≥60 题、`tool` ≥40 题、`multi-turn` ≥20 剧本。

3. 记录：模型名、日期、温度、采样数、数据集版本、索引 hash、延迟、token、估算费用。

4. 门槛在**首轮可信基线之后**确定，本文不预填百分比。

### 阶段 6：CI（0.5 天）

见 §12。**准入**：阶段 1~4 稳定运行两周无 flaky。

### 依赖关系

```text

阶段0 ──► 阶段1 ──► 阶段2 ──► 阶段3 ──► 阶段4 ──► 阶段6

                     └────────────► 阶段5（可与4并行，但依赖2的协议契约）

```

不要先跑在线评测代替基础回归：`eval_report.py` 一次运行 = 12 题 × 3 采样 × 3~4 次调用 ≈ 100+ 次付费请求，却无法定位任何确定性缺陷。

---

## 12. CI 建设建议（GitHub Actions）

**每个 PR 必跑（离线，<3 分钟）**：

```yaml

- uv sync --frozen --dev          # 锁文件必须一致

- uv run pytest -q                # 默认排除 online/ai_eval/slow

- git diff --check                # 空白/冲突标记

- uv lock --check

```

要点：

1. 缓存 `~/.cache/uv` 与 `~/.cache/huggingface`（后者只给可选 job）。

2. 离线 job **不注入** `DEEPSEEK_API_KEY`，让误联网直接失败而不是静默计费。

3. `python-version: 3.14`（与 `.python-version` 一致）；矩阵可加 3.14 单版本即可，避免无意义组合爆炸。

4. 测试结果用 `--junitxml` 上传，失败可追溯到 Test ID。

**定时 / 手动 job（不进 PR 阻塞）**：

- `online-smoke`：每日 1 次，五类各 1 题 + 多轮剧本，只验协议与 schema。

- `ai-eval`：每周 1 次，手动触发，需 `secrets.DEEPSEEK_API_KEY`、费用上限、报告 artifact 留档（含模型版本与索引 hash）。

- `embedding-cache`：仅在需要真实 BGE 的 job 中恢复模型缓存，避免每次下载 100MB。

**门槛策略**：先只做"报告 + 人工看"，等连续 4 周基线稳定后再对**回归**（相对上期下降超阈值）告警，而不是设绝对分数线。

---

## 13. 尚未确认的问题与风险（需你决策）

1. **`store=None` 且路由到 RAG 分支**：继续抛 `AttributeError`（视为开发错误），还是返回可识别降级文案？无论哪种，都不应直接调 `None.search()`。

2. **RAG `content=None`**：抛异常、返回空串、还是降级文案？当前会写入历史并污染下一轮。

3. **Agent 达 `max_rounds`**：改为抛明确异常（保持 `(str, list)` 契约不破），还是返回结构化状态（会波及 demo/eval 调用方）？

4. **validator 语义**：只校验"最终输出"需要标识最终值；当前按工具名挂载导致中间量误报。是加"最终值"标记、拆专用工具，还是取消自动电压校验？仅改文案不解决问题。

5. **`execute_tool` 精度**：`:g` 六位有效数字是否可接受？改成 `repr(value)` 会改变模型看到的字符串与既有 demo 输出。

6. **工具异常边界（已按当前实现定标）**：非实数结果由 `calculate_expression()` 以稳定 `ValueError` 拒绝，残缺表达式保留 `SyntaxError`；两者均由 `execute_tool()` 转成"计算失败：…"。未预期的其他 `TypeError` 仍向上抛出，不在本项中宽泛捕获。

7. **布尔字面量**：`True` 当前算作 `1`，是否收紧为非法？

8. **全角字符清洗**：修复会改变 Chunk 文本，必须重建索引并重跑检索基线；是否与"索引版本化"一起做？

9. **`context()` 返回内部列表**：接受为公开语义，还是改为副本？改副本是行为变更，需单独验收。

10. **超时与重试**：是否显式配置（当前继承 `openai 3.37.0` 默认 `read=600s / retries=2`）？显式化后才算项目契约。

11. **评测独立性**：是否引入异源裁判，或"规则 + 人工盲审 + DeepSeek"组合？同源偏差目前无量化。

12. **评测集授权**：黄金集能否使用课件衍生内容？仓库外课件如何版本化？未定前检索指标不可完全复现。

13. **质量门槛**：在拿到首轮可信基线前，本文不设任何百分比；由你决定是"只看趋势"还是"设回归阈值"。

---

## 14. 本次审计的执行记录（可追溯）

**已阅读**：`AGENTS.md`、`README.md`、`docs/codebase-overview.md`、`docs/testing/TEST_PLAN.md`（初版）、`pyproject.toml`、`.env.example`，以及 `src/aurora/` 全部 15 个文件、`scripts/` 全部 9 个文件、`data/eval_set.json`、`data/index/chunks.json`。

**历史审计时点已执行的离线探针**（一次性命令，未写成测试文件，未联网；下表保留当时结果，不代表当前实现）：

| 探针 | 结果 |

|---|---|

| `execute_tool("round(3.14159, ndigits=2)")` | `"3"`（关键字被丢弃） |

| `execute_tool("123456789")` / `("100/3")` / `("2**40")` | `"1.23457e+08"` / `"33.3333"` / `"1.09951e+12"` |

| `execute_tool("1+")` | 抛 `SyntaxError`（未捕获） |

| `execute_tool("(-8)**(1/3)")` | 抛 `TypeError`（未捕获） |

| `execute_tool("log(0)")` / `("sqrt(-1)")` / `("1/0")` / `("10**10000")` | 均返回 `"计算失败：…"` |

| `execute_tool("True")` / `("False")` | `"1"` / `"0"` |

| 危险语法 5 例 | 全部 `ValueError`，无副作用 |

| `validate_opamp_output("15", 15.0)` / `("-15.01", 15.0)` | `None` / 正确负向钳位警告 |

| `chunk_text("输出＝１５Ｖ，正常")` | `"输出正常"` |

| `chunk_text("１.2 标题行\n正文…")` | `heading == ""`（全角数字标题未被识别） |

| `VectorStore.search(k=-1)` / `k=0` / 空库 | `[第1,第3]` / `[]` / `[]` |

| `openai` 版本与默认值 | `3.37.0`，`connect=5s`、`read=600s`、`max_retries=2` |

| patch `aurora.config.OpenAI` 后调用 `aurora.router.get_client()` | 返回替身（证明"用 `from ... import get_client` 绑定本地引用"仍可被该点统一熔断） |

| 索引统计 | 24 Chunk（课件 16 + 卡片 8），10 个课件来源 |

**审计时明确未执行**：`pytest`（当时未安装）、`scripts/eval.py`、`scripts/eval_report.py`、`scripts/build_index.py`、任何 `demo_*.py`、任何真实模型调用。因此该次审计**不存在任何"测试通过"结论**。当前 pytest 虽已安装，但仍未执行测试套件。

**范围声明**：本文的审计与设计过程未修改业务代码或创建 `tests/` 目录；后续仅将 pytest 纳入开发依赖并更新锁文件。

---

## 15. 本计划的完成标准

阶段 1 完成的标准不是"用例数量"，而是：**阶段 1 范围内的每个已知 P0 缺陷都有稳定复现或定标用例，并满足本阶段准入条件**。在此之前，`12/12` 这类数字不构成质量证据。
