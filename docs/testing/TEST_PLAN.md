# Aurora 测试总计划

> 本文只描述测试设计与预期行为，不记录进度。
>
> - 用例是否实现：看 `tests/` 下的函数名（其中含 Test ID）
> - 是否执行、是否通过：见 `TEST_STATUS.md`；判定与例外见 `runs/`
> - 谁在何时为何修改：看 `git log`

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

模块清单、公开接口、职责与外部依赖见 `docs/codebase-overview.md` §4「模块地图」；
测试视角的确定性归类见 §4.2 第 1 条与 §5 矩阵。

---

## 3. 核心业务调用链路

主链与分支的逐行说明见 `docs/codebase-overview.md` §5「完整问题处理链路」。本节只保留测试契约需要的两点：外部调用次数与状态写回规则。

### 3.1 分支与外部调用次数（首轮 / 有历史时）

| 分支 | 处理 | 首轮 LLM 调用 | 多轮 LLM 调用 | `trace` | `hits` |
|---|---|---|---|---|---|
| `opamp` | Tool Agent + 电压校验 | 1 + N（N≥1，上限 5） | 2 + N | `list` | `None` |
| `feedback` | RAG + 决策树提示词 | 2 | 3 | `None` | `list` |
| `power` | RAG + 链式流程提示词 | 2 | 3 | `None` | `list` |
| `concept` | RAG + 默认提示词 | 2 | 3 | `None` | `list` |
| `fallback` | 模板文案 | 1 | 2 | `None` | `None` |

（"2"= Router + 生成；多轮多一次改写。`opamp` 的改写结果只被 Router 消费，属纯开销。）

### 3.2 状态写回的真实规则

- 写回点唯一：`pipeline.run()` 尾部 `session.add_turn()`，五条分支共用。

- 抛异常发生在写回之前 → Session 长度不变（`rewrite.py` 的异常在内部已吞掉，不中断）。

- **但**还有两类"假成功"写回路径：`ask_rag()` 在 `content is None` 时返回 `None`、`run_agent()` 达 `max_rounds` 返回占位文案。处置已于 2026-11-22 定案（§13 第 2、3 条：都改为写回前的明确异常），现登记为缺陷 DEF-06、DEF-07，断言见 §6.6 `RAG-INT-005` 与 §6.9 `PIP-INT-005`。

- 空索引时 `ask_rag()` 返回"课件库还是空的…"字符串；该文案是用户实际看到的最终回答，已决定保留写入 Session，见 §13 第 13 条。

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

6. **避免默认产生真实调用与费用**：三层防线的实施位置、防护范围、不覆盖的情况与自检方法见 `TESTING_GUIDE.md` §8。本节的结论只有一条——默认执行（不带任何 marker）下不允许出现网络请求、真实模型加载或 API 计费，任何例外都必须同时满足 marker 与凭据两道前置条件。

7. **隔离外部依赖**：

   - 文件系统：一律 `tmp_path`；禁止写 `data/index/`、`.env`、`../courseware_md/`；对 `VectorStore.save/load` 使用显式目录参数而不是模块常量。

   - 数据库：无。

   - 环境变量：`config.py` 在导入时冻结常量，因此**不要靠 `monkeypatch.setenv` 改行为**（导入顺序会污染），直接 `monkeypatch.setattr("aurora.config.DEEPSEEK_API_KEY", None)`。

   - 模型缓存：`embedder.get_model` 带 `lru_cache(maxsize=1)`，相关用例前后必须 `get_model.cache_clear()`，否则 patch 会跨用例泄漏。

8. **测试互不影响**：每个用例新建 `Session` / `VectorStore` / fake client；fake client 用"响应队列按调用顺序弹出"，队列耗尽即抛错（能捕获多调用/少调用）；不复用可变 history；不共享模块级常量（除只读的 `CIRCUIT_TYPES`）。

9. **Fixture 与数据组织**：见 §8。原则是"软件测试的微型数据"与"AI Eval 的黄金集"物理分离，后者不进默认 CI。

10. **适合进 CI**：全部 Unit + Integration + 离线 E2E；`git diff --check`；`uv lock --check`。在线 E2E 与 AI Eval 用手动/定时工作流。

### 4.3 Mock 的正确落点

各业务模块用 `from aurora.config import get_client` 绑定本地引用后直接调用，没有注入点。因此补丁必须打在**使用方模块**（`aurora.router.get_client`、`aurora.rag.encode_query`、`aurora.agent.execute_tool`、`aurora.embedder.get_model`），patch `aurora.config.get_client` 无效。补丁落点规范见 `TESTING_GUIDE.md` §5，全局兜底与用例级注入的分工见同一文件 §8。

### 4.4 缺陷定标（pin）约定

P0 里有若干用例，其"当前行为"本身就是缺陷（`content=None` 被写回、轮数耗尽被当成功、负 `k` 切片丢最低分……）。这类用例必须二选一，不能含糊，否则要么把 bug 锁死，要么让阶段准入永远红：

| 类型 | 断言什么 | pytest 写法 | 适用用例 |
|---|---|---|---|
| **A. 缺陷定标** | 断言**正确行为**（缺陷存在时必然失败） | `@pytest.mark.xfail(strict=True, reason="DEF-xx 缺陷描述")` | RTR-UNIT-005、RTR-UNIT-006、RAG-INT-004、RAG-INT-005、PIP-INT-005、AGT-UNIT-004①④ |
| **B. 现状锁定** | 断言**当前行为**（含错误值），并注明修复时同步改断言 | 普通 `assert` + `# 修复 DEF-xx 后改此断言` | TOL-UNIT-004（`:g` 精度）、VST-UNIT-002（负 k）、CHK-UNIT-001（全角清洗）、SES-UNIT-002（返回内部 list） |

不用 `skip` 的理由：`skip` 会让缺陷永久沉默；`xfail(strict=True)` 在缺陷修复后转为 `XPASS` 并**直接报错**，强制作者回来删标记，缺陷不会在修复后继续"假装红着"。

每一项缺陷定标用例都要在 `reason` 里带缺陷编号，且必须能从 `docs/testing/DEFECTS.md` 的「关联 Test ID」列查回对应用例，避免缺陷修复后没人回头改测试。定标用例的缺陷编号必须先在 `DEFECTS.md` 登记才能使用。

A 类列出的用例若在 `DEFECTS.md` 中查不到对应缺陷，不得计入阶段准入：

1. 默认保持 `Planned`，暂不写用例；
2. 确需先留下可复现的记录时，可以写成 `@pytest.mark.xfail(strict=True, reason="§13-N 待决策")`，同样不计入阶段准入；
3. 决策落地后先在 `DEFECTS.md` 登记缺陷，再按结果转 A 类，或改断言转 B 类并同步注释。

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
>
> Status 列已删除：本节的表只描述用例设计（目标、输入、期望、依赖、Mock 策略、优先级）。是否实现以 `tests/` 下含 Test ID 的函数名为准；当前执行判定与证据入口见 `docs/testing/TEST_STATUS.md`。

### 6.1 配置层 `config.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| CFG-UNIT-001 | config | Unit | 缺 key 时明确失败 | `monkeypatch` 置 `DEEPSEEK_API_KEY=None` | 抛 `RuntimeError`，消息含 `.env` 指引；不构造客户端 | 无 | patch 模块常量，不读真实 `.env` | P0 |
| CFG-UNIT-002 | config | Unit | 客户端构造参数 | 固定 key / base_url / model | `OpenAI` 收到 `api_key` 与 `base_url`；两次 `get_client()` 返回不同实例（当前不复用） | openai SDK | patch `aurora.config.OpenAI` 为记录参数的替身 | P1 |
| CFG-UNIT-003 | config | Unit | 项目未显式配置超时与重试 | `get_client()`，不传任何超时/重试参数 | 构造 `OpenAI` 的 kwargs 只有 `api_key` 与 `base_url`，不含 `timeout`/`max_retries`——即项目未覆盖 SDK 默认；具体继承值不写死在本行；需要数值核对时现场构造观测，不写入档案 | openai SDK（版本由 `uv.lock` 决定） | patch `aurora.config.OpenAI` 为记录 kwargs 的替身，断言 kwargs 不含 `timeout`/`max_retries` | P1 |

### 6.2 上下文改写 `rewrite.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| RWT-UNIT-001 | rewrite | Unit | 首轮零成本门控 | `history=None` / `[]` | 原样返回；`get_client` **未被调用**（用会抛异常的哨兵验证） | 无 | 不 patch，用哨兵 | P0 |
| RWT-UNIT-002 | rewrite | Unit | 历史窗口与截断 | 5 轮共 10 条；某条 400 字；role 为 `system` | 只取最后 4 条；长文本截到 300 字 + `…`；非 `user` 显示"Aurora"；换行被压成空格 | 无 | 直接测 `_format_history` | P1 |
| RWT-UNIT-003 | rewrite | Unit | 异常与异常输出回退 | ①`get_client` 抛 `RuntimeError` ②`content=None` ③`content=""` ④`content` 501 字 ⑤正常 20 字带引号 | ①②③④ 返回原问题且不抛异常；⑤ 去掉首尾空白与中英文引号 | 无 | patch `aurora.rewrite.get_client` | P0 |
| RWT-UNIT-004 | rewrite | Unit | "只有说话人标签"的历史 | `history=[{"role":"x","content":None}]` → `_format_history` 产出 `"Aurora："`（非空字符串、无正文） | 现状：字符串非空 → **仍会调用模型**。需先定义"有效历史文本"= 至少一条正文非空，否则直接返回原问题 | 无 | fake + 调用计数 | P2 |
| RWT-INT-001 | rewrite→pipeline | Integration | 改写只污染查询侧 | 伪造 standalone 与原问题不同 | `route` 与 `ask_rag(query=)` 收到 standalone；`run_agent` 的 question、`ask_rag(question=)`、`add_turn` 收到原话 | pipeline | patch `route/run_agent/ask_rag`，捕获实参 | P0 |
| RWT-AI-001 | rewrite | AI Eval | 指代消解与数值保真 | 数据集：代词、省略、参数替换、"换话题" | 报告 exact/semantic accuracy、数值与单位保真率、错误改写率；失败样本人工复核 | 真实 LLM | 不 Mock，独立付费作业 | P1 |

### 6.3 Router `router.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| RTR-UNIT-001 | router | Unit | 合法结构化输出 | tool_call 参数 `{"circuit_type":"opamp","confidence":0.9,"reason":"求 uo"}` | 返回三字段；`confidence` 为 `float`；`reason` 原样 | 无 | patch `aurora.router.get_client` | P0 |
| RTR-UNIT-002 | router | Unit | 无 tool_call 兜底 | `message.tool_calls=[]` 或 `None` | `{"circuit_type":"concept","confidence":0.0,"reason":"Router 未返回有效分类"}` | 无 | fake | P0 |
| RTR-UNIT-003 | router | Unit | 坏 JSON 兜底 | `arguments="{oops"` | `concept / 0.0 / "分类参数解析失败"` | 无 | fake | P0 |
| RTR-UNIT-004 | router | Unit | 非法枚举 | `circuit_type="unknown"`；缺失该字段 | 均回退 `concept`；缺失时 `reason` 仍保留 | 无 | fake（两份响应参数化） | P0 |
| RTR-UNIT-005 | router | Unit | 非 dict JSON 暴露类型契约 | `arguments="[]"` / `"\"opamp\""` / `"null"` | 现状：`args.get` 抛 `AttributeError`，Router 不兜底 → 中断 Pipeline。目标：与其他坏输出一致回退 | 无 | fake | P0 |
| RTR-UNIT-006 | router | Unit | `confidence` 类型契约 | `"high"` / `null` / `2.5` / `-1` / 缺失 | 现状：`"high"`→`ValueError`；`null`→`TypeError`；`2.5`/`-1` 不钳位；缺失→`0.0`。修复后应统一为"非数值回退 0.0 + 钳位到 [0,1]" | 无 | fake（5 份响应参数化） | P0 |
| RTR-UNIT-007 | router | Unit | 请求参数契约 | 任意问题 | `tool_choice` 强制 `route_circuit`；`temperature=0.0`；`tools=[ROUTE_TOOL]`；system 消息含五类说明 | 无 | fake 记录 kwargs | P2 |
| RTR-INT-001 | router→pipeline | Integration | 分类结果驱动分支 | 五类固定 Router 输出 | 只调用对应能力：`opamp`→`run_agent`；`feedback/power`→`ask_rag` 且 `system_prompt` 分别为 `FEEDBACK_PROMPT`/`POWER_PROMPT`；`concept`→`system_prompt is None`；`fallback`→不调用任何能力 | pipeline | patch `aurora.pipeline.route` 与三个能力函数 | P0 |
| RTR-E2E-001 | router | E2E | 分类→分支→返回字段一致 | 五类问题各一（fake LLM 按脚本回复） | 返回 dict 七字段齐全；`circuit_type` 与分支产物匹配（`trace`/`hits` 互斥且正确） | 全部 | 离线 fake | P0 |
| RTR-AI-001 | router | AI Eval | 五类分类质量与混淆 | 分层标注集（同义改写/边界/超纲），≥150 题 | 报告 accuracy、macro-F1、每类 recall、混淆矩阵、置信度校准（ECE） | 真实 DeepSeek | 不 Mock；≥3 次采样取多数 | P0 |
| RTR-AI-002 | router | AI Eval | 标签定义一致性 | 人工构造"跨类"问题（如"负反馈对运放输出有什么影响"） | 明确该归 `feedback` 还是 `opamp`，形成判定规范；统计"规范歧义题"占比并单列 | 标注规范 | 双人标注 | P2 |

### 6.4 知识入库数据层 `chunker.py` / `card_loader.py` / `embedder.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| CHK-UNIT-001 | chunker | Unit | 私有区与全角字符清洗 | `"输出＝１５Ｖ，正常"`、含 PUA 码点行、含 `\|` 表格行 | 清洗为 `"输出正常"`（全角 `＝１５Ｖ` 因 ≥U+E000 被删，**已知数据损失**）；`\|` 变空格；纯符号行被丢弃 | 无 | 无 | P0 |
| CHK-UNIT-002 | chunker | Unit | 可读性与占位过滤 | 纯英文行、纯公式行、含"请替换文字"的行、含任意一个汉字的行 | 前 3 类被丢弃；含汉字行保留 | 无 | 无 | P0 |
| CHK-UNIT-003 | chunker | Unit | 标题识别与继承 | `"1 集成运算放大器"`、`"１.2 标题"`（全角数字）、`"2026 年考试"` | ASCII 数字开头且 ≤30 字 → 成为 heading 且不进正文；全角数字被清洗成 `".2 标题"` → **不识别为标题**，heading 为空串；误判为标题的行不进正文 | 无 | 无 | P1 |
| CHK-UNIT-004 | chunker | Unit | 600 字封口与残段 | 多段正文累计跨过阈值；末尾不足阈值 | 达到阈值即 `flush`；末段 `flush`；chunk 的 `source` 为文件名、`heading` 为最近标题；注意阈值按"无空格拼接长度"计算，实际 text 略长 | 无 | 无 | P1 |
| CHK-UNIT-005 | chunker | Unit | 编码与异常 | 非 UTF-8 文件；不存在的路径 | `chunk_file` 抛 `UnicodeDecodeError` / `FileNotFoundError`，不静默返回空列表 | 文件系统 | `tmp_path` | P2 |
| CRD-UNIT-001 | card_loader | Unit | 多卡解析 | 含前言、3 张 `##` 卡、字段行、空行的 md | 3 个 `Chunk`；`text` 以"标题。"开头并拼接字段；`source="公式卡片：<标题>"`；`heading=标题` | 文件系统 | `tmp_path` 写临时 md | P0 |
| CRD-UNIT-002 | card_loader | Unit | 空卡与异常结构 | 只有标题无正文；连续两个 `##`；`#` 一级标题 | 空卡仍生成 Chunk（text 仅标题）；一级标题不参与解析；不抛异常 | 文件系统 | `tmp_path` | P1 |
| EMB-UNIT-001 | embedder | Unit | 查询/文档调用契约 | 一批 passages + 单条 query | passages **不加**前缀、`normalize_embeddings=True`、`show_progress_bar=False`、`convert_to_numpy=True`；query **加** `QUERY_INSTRUCTION`；两者 `dtype=float32` | sentence-transformers | patch `aurora.embedder.get_model` 为记录入参的假模型 | P0 |
| EMB-UNIT-002 | embedder | Unit | 形状与缓存 | 3 条文本；连续两次 `get_model()` | 返回 `(3, D)`；`get_model` 走 `lru_cache` 只加载一次；用例前后 `cache_clear()` | 无 | 同上 | P1 |

### 6.5 向量库 `vector_store.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| VST-UNIT-001 | vector_store | Unit | top-k 排序正确 | 3 个二维单位向量 + 查询 `[1,0]` | 分数降序；`score` 与手算点积一致；返回 `SearchResult` | NumPy | 无 | P0 |
| VST-UNIT-002 | vector_store | Unit | k 的边界 | `k=1`、`k=3`、`k=5`（>N）、`k=0`、`k=-1` | `1/3/3/[]`；`k=-1` 返回 `[第1,第3]`（切片 `[:-1]` 丢最低分）→ 作为当前语义固定，修复时改显式校验 | NumPy | 无 | P0 |
| VST-UNIT-003 | vector_store | Unit | 空库与 `None` 向量 | `VectorStore()`；`vectors=None` | 返回 `[]`，不抛异常 | NumPy | 无 | P0 |
| VST-UNIT-004 | vector_store | Unit | 形状/数量不一致 | query 维度 ≠ 向量维度；`len(chunks) != len(vectors)` | 前者 `ValueError`；后者当前**不报错**，按位置取会导致错配 → 记录为契约缺陷 | NumPy | 无 | P1 |
| VST-INT-001 | vector_store | Integration | 保存/加载往返 | 2 个 Chunk + 固定矩阵 → `tmp_path` → `load` | 文本、`source`、`heading`、顺序、dtype、检索结果全等；不触碰 `data/index/` | 文件系统 | `tmp_path` | P0 |
| VST-INT-002 | vector_store | Integration | 坏持久化输入 | 缺 `vectors.npy`；`chunks.json` 非法；两者数量不匹配 | 前两者抛 `FileNotFoundError` / `JSONDecodeError`；第三者当前可加载成功 → 需在加载时校验 | 文件系统 | `tmp_path` | P1 |
| VST-INT-003 | vector_store | Integration | `add()` 契约 | 空库 `add` 两次；有 `vectors=None` 时 `add` | 向量 `vstack` 后形状 `(2,D)`；`len(store)==2`；注意 `add` 目前**无调用方**，仅锁定行为 | NumPy | 无 | P2 |

### 6.6 RAG 在线链 `rag.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| RAG-UNIT-001 | rag | Unit | 上下文拼接格式 | 2 个 `SearchResult`（source A/B） | 编号从 1 起、含"（来源：X）"、顺序与 hits 一致、正文完整无截断 | 无 | 无 | P0 |
| RAG-UNIT-002 | rag | Unit | 空 hits 文案 | `build_context([])` | 返回 `""`；`ask_rag` 在空 hits 时返回"课件库还是空的…"且**不调用** `get_client` | 无 | 哨兵 client | P0 |
| RAG-INT-001 | rag | Integration | query 与生成输入隔离 | `question="那 R1 换成 20k 呢"`、`query="反相比例电路 R1=20k 求 uo"`、含 history | `encode_query` 收到 query；user 消息含原问题与全部片段；history 顺序为 system→history→user；history 未被原地修改 | VectorStore 真、Embedding/LLM 假 | patch `aurora.rag.encode_query` 与 `get_client` | P0 |
| RAG-INT-002 | rag | Integration | 自定义提示词生效 | `system_prompt="X"` / `None` | 分别使用 `"X"` 与 `RAG_SYSTEM_PROMPT`；`temperature=0.3` | 同上 | 同上 | P0 |
| RAG-INT-003 | rag | Integration | top_k 透传 | `top_k=1` / `5` / `0` | 传入 `store.search` 的 k 与返回值长度一致；`k=0` → 空 hits → 走"课件库还是空的"分支 | VectorStore | 假 encoder 返回固定向量 | P1 |
| RAG-INT-004 | rag→pipeline | Integration | `store=None` 的真实失败面 | Router 判 `concept`，`run(question, None)` | 已定案（2026-11-22，§13 第 1 条）：进入 RAG 分支前显式抛 `ValueError`（消息含 `VectorStore`），不调用 `None.search`，Session 不写回。现状仍为 `AttributeError` → DEF-05，`xfail(strict=True)` 定标 | pipeline | patch Router，其余真实 | P0 |
| RAG-INT-005 | rag→session | Integration | `content=None` 污染多轮状态 | fake 生成响应 `content=None` | 已定案（§13 第 2 条）：抛 `RuntimeError("模型没有返回有效内容")`，Session 长度不变。现状把 `answer=None` 写入（`len(session)==2`、第二条 `content is None`）→ DEF-06，`xfail(strict=True)` 定标 | pipeline、session | patch Router + `aurora.rag.get_client` | P0 |
| RAG-INT-006 | rag | Integration | 检索分数非归一化风险 | 未归一化向量入库 | 点积不再等于余弦；记录该前置条件必须在 `embedder` 侧保证 | NumPy | 无 | P2 |
| RAG-AI-001 | retrieval | AI Eval | 检索质量 | 标注 `query → 相关 chunk/source`，含"无答案"题 | 报告 Recall@1/3/5、MRR、nDCG@5；"无答案误检率"单列；固定索引版本与 hash | 真实 BGE + 冻结索引 | 不 Mock | P0 |
| RAG-AI-002 | generation | AI Eval | 正确性/忠实度/引用 | concept/feedback/power 代表题 + 片段不足的对抗题 | 报告答案正确率、faithfulness、诚实拒答率、citation precision/recall | 真实 LLM | 不 Mock；规则校验 + 人工抽检 | P0 |
| RAG-AI-003 | ingestion | AI Eval | 切块策略的影响 | 固定语料，对比 600 字 vs 其他阈值、有无卡片 | 记录 Recall/MRR 差异；单次波动不作为回归判据 | 真实 Embedding | 离线实验，产物隔离 | P2 |

### 6.7 工具层 `tools.py`（安全边界，P0 最密集）

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| TOL-UNIT-001 | tools | Unit | 白名单正常计算 | `-(100/10)*(-3)`、`3**4+sqrt(2)*sqrt(8)`、`pi`、`e`、`-2`、`7%3` | 分别得 `30.0`、`85.0`、`π`、`e`、`-2`、`1`；返回 `float` | 无 | 无 | P0 |
| TOL-UNIT-002 | tools | Unit | 拒绝危险语法 | `__import__("os").system(...)`、`(1).__class__`、`[1,2][0]`、`lambda:1`、`a=1`、字符串拼接 | `a=1` 在 `ast.parse(..., mode="eval")` 阶段抛原生 `SyntaxError`；其余可成功解析但不在 AST 白名单中的表达式抛 `ValueError`；全部被拒且无副作用 | 无 | 无 | P0 |
| TOL-UNIT-003 | tools | Unit | 关键字参数不得静默改写语义（DEF-01） | `round(3.14159, ndigits=2)`；`round(3.14159, 2)` | 前者返回 `计算失败：不支持关键字参数…`（显式拒绝），后者返回 `"3.14"`；不允许再出现静默丢弃后返回 `"3"` | 无 | 无 | P0 |
| TOL-UNIT-004 | tools | Unit | 精度截断 | `123456789`、`100/3`、`2**40` | 返回 `"1.23457e+08"`、`"33.3333"`、`"1.09951e+12"`（`:g` 仅 6 位有效数字）→ 测死当前行为，修复后应保留足够精度 | 无 | 无 | P1 |
| TOL-UNIT-005 | tools | Unit | 异常转换矩阵 | `1/0`、`log(0)`、`sqrt(-1)`、`10**1000` | 全部返回 `"计算失败：…"` 字符串 | 无 | 无 | P0 |
| TOL-UNIT-006 | tools | Unit | 预期表达式错误转换（DEF-02） | `(-8)**(1/3)`（复数结果）；`"1+"`（`SyntaxError`）；`sqrt()`（参数个数不符的 `TypeError`） | `calculate_expression()` 对非实数抛 `ValueError("计算结果不是实数")` 并保留 `ast.parse()` 原生 `SyntaxError`；`execute_tool()` 分别转为 `计算失败：计算结果不是实数`、`计算失败：表达式语法错误`、`计算失败：参数不匹配…`，均不向 `run_agent()` 逃逸 | 无 | 无 | P0 |
| TOL-UNIT-007 | tools | Unit | 分派与参数校验 | 未知工具名；`arguments` 非 dict；`expr` 缺失/空串/非字符串 | 返回可识别错误字符串，不抛异常 | 无 | 无 | P0 |
| TOL-UNIT-008 | tools | Unit | 布尔字面量类型放宽 | `"True"` / `"False"` | 返回 `"1"` / `"0"`（`bool` 是 `int` 子类）→ 待定是否收紧为拒绝 | 无 | 无 | P2 |
| TOL-UNIT-009 | tools | Unit | 运放校验边界与符号 | `15`、`-15`、`15.01`、`-20`、`"错误：…"`、`"abc"`、`supply=12` | 边界值不告警；越界告警且符号 `+/-` 正确；非数值返回 `None`；`supply` 参数生效 | 无 | 无 | P0 |
| TOL-UNIT-010 | tools | Unit | 资源边界（隔离执行） | `10**10000000`、`9**9**9`、`(10**1000)**1000`、200 层嵌套括号 | `calculate_expression()` 在解析前拒绝超长表达式（>300 字符）；`_eval_pow()` 限制指数绝对值（>1000）与整数幂结果位数（≈10 万 bit）；两者均经 `execute_tool()` 转为 `计算失败：…`。子进程 + 20s 超时执行，断言主进程不受影响；标 `slow`，不进默认 CI | OS | 子进程 | P2 |
| TOL-AI-001 | tools/agent | AI Eval | 工具调用准确率 | 需算/无需算、单步/多步、单位干扰、非法表达式 | 报告"是否调用工具"决策准确率、工具名与参数 exact match、执行成功率、最终数值正确率 | 真实 LLM + 本地工具 | 不 Mock，独立作业 | P0 |

### 6.8 Agent 回环 `agent.py`

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| AGT-UNIT-001 | agent | Unit | 单轮工具 + 终答 | 第 1 次返回 1 个 tool_call，第 2 次返回 `content` | 返回 `(content, trace)`；messages 顺序 `system→user→assistant(tool_calls)→tool`；trace 长度 1 | 无 | scripted fake | P0 |
| AGT-UNIT-002 | agent | Unit | 一次多个 tool_calls | 单次返回 2 个 tool_call | 生成 2 条 `tool` 消息且 `tool_call_id` 各自对应；trace 顺序与调用一致 | 无 | scripted fake | P0 |
| AGT-UNIT-003 | agent | Unit | 坏参数与未知工具 | `arguments="{oops"`；`name="unknown_tool"` | 坏 JSON 变 `{}` → 工具返回"缺少字符串参数"；未知工具返回"未知工具"字符串；均作为 `tool` 消息回传，不抛异常 | tools | scripted fake | P0 |
| AGT-UNIT-004 | agent | Unit | 终止与异常 | ①连续 5 轮只返回 tool_call ②`finish_reason="length"` 且无内容 ③无 tool_call 且 `content=None` 且 `finish_reason="stop"` ④`finish_reason="length"` 且 `content="…半截"` | ① 目标抛 `RuntimeError`（消息含「最大工具调用轮数」）；现状返回占位文案 + 完整 trace → DEF-07 ② `RuntimeError("模型输出达到长度限制")` ③ `RuntimeError("模型没有返回有效内容")` ④ 目标抛 `RuntimeError("模型输出达到长度限制")`；现状按正常回答返回半截内容 → DEF-08。①④ 已按 §13 第 3 条定案，`xfail(strict=True)` 定标 | 无 | scripted fake | P0 |
| AGT-UNIT-005 | agent | Unit | validator 挂载与消息拼装 | `validators={"calculate_expression": f}`，f 返回 `"WARN"` | `tool` 消息内容为 `"<result>\n[合理性校验] WARN"`；trace 同时含 `result` 与 `warning`；无 validator 时内容仅为 `result` | 无 | scripted fake + 直接调用 | P0 |
| AGT-UNIT-006 | agent | Unit | history 只接受纯文本 | `history` 含 `tool` 角色消息 | 现状：原样 `extend` 进 messages，不做过滤 → 记录为契约风险（应由调用方保证） | 无 | scripted fake 记录 messages | P2 |
| AGT-INT-001 | agent→tools | Integration | 真实执行 + 校验回传 | 假 LLM 先请求 `-(100/10)*(-3)`，再出终答 | trace: `result="30"`、`warning` 含"钳位在 +15V"；第 2 次请求的末条 `tool` 消息含该警告 | 真实 tools | 只 patch `aurora.agent.get_client` | P0 |
| AGT-INT-002 | agent→tools | Integration | 中间量误触发电压校验 | 先 `100000*3`，再 `0.5*10` | 已定案（2026-11-22，§13 第 4 条）：暂按现状锁定——**每次** `calculate_expression` 都跑 validator，第 1 步即产生"饱和"警告；作为已知风险记录，不写成成功契约。改为「只校验最终输出」或「取消按工具名自动校验」需另立任务 | 真实 tools | scripted fake 捕获每轮消息 | P0 |
| AGT-INT-003 | agent→tools | Integration | 工具异常不外溢 | 假 LLM 请求 `(-8)**(1/3)` | 非实数运算错误不外溢：以 `tool` 消息与 trace 回传，Agent 继续下一轮并返回最终回答 | 真实 tools | scripted fake | P0 |

### 6.9 Pipeline 编排与端到端

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| PIP-UNIT-001 | pipeline | Unit | 降级文案 | `fallback_answer("用 Python 写快排")` | 含原问题、能力清单、学习路径；纯函数无副作用 | 无 | 无 | P0 |
| PIP-UNIT-002 | pipeline | Unit | 提示词分派表 | `RAG_PROMPTS` 的键集合 | 恰为 `{feedback, power}`；`concept` 不在表中（回落到默认提示词）；`OPAMP_VALIDATORS` 只挂 `calculate_expression` | 无 | 无 | P1 |
| PIP-INT-001 | pipeline | Integration | 五分支统一返回 | 参数化五类 + `top_k=5` + 有 Session | 七字段齐全且类型正确；`trace`/`hits` 互斥；`session` 恰好 +2 且为原话 + 最终答案 | 下游替身 | patch `route` 与三个能力函数 | P0 |
| PIP-INT-002 | pipeline | Integration | 下游异常传播且不写回 | ①`route` 抛错 ②`run_agent` 抛错 ③`ask_rag` 抛错 ④`rewrite` 抛错 | ①②③ 异常向上传播且 `len(session)==0`；④ 回退原问题并继续执行 | 下游替身 | 参数化 patch | P0 |
| PIP-INT-003 | pipeline | Integration | fallback 零外部调用 | Router 固定 `fallback` | `run_agent`/`ask_rag` **未被调用**（哨兵）；`trace=hits=None` | 无 | 哨兵替身 | P0 |
| PIP-INT-004 | pipeline | Integration | 临时 Session 无跨调用泄漏 | 连续两次 `run(q, store)` 不传 session | 两次的 `history` 均为 `[]`；无全局可变状态 | pipeline | patch 能力函数捕获 `history` | P0 |
| PIP-INT-005 | pipeline | Integration | 轮数耗尽被当成功 | opamp 分支 fake LLM 5 轮都只调工具 | 已定案（2026-11-22，§13 第 3 条）：`run_agent` 抛 `RuntimeError`（消息含「最大工具调用轮数」），Pipeline 不写回，Session 长度不变。现状占位文案写入 Session、`answer` 与正常答案无法区分 → DEF-07，`xfail(strict=True)` 定标 | agent、session | scripted fake | P0 |
| PIP-E2E-001 | pipeline | E2E | 离线五类冒烟 | 5 个代表问题 + 3 个二维向量的小索引 + scripted LLM | 每类命中正确分支；返回 schema 与 `demo_*.py` 的消费方式兼容；Session 各 +2 | 全链 | 统一 fake，不联网 | P0 |
| PIP-E2E-002 | pipeline | E2E | 空索引全链 | 空 `VectorStore` + concept 路由 | 返回"课件库还是空的…"，`hits==[]`，Session +2；降级文案作为用户实际看到的回答写入历史（§13 第 13 条已定案） | 全链 | fake | P1 |
| PIP-E2E-003 | pipeline | E2E | 在线冒烟（少量） | 五类各 1 题 + 1 个多轮剧本 | 请求成功、schema 完整、无未处理协议错误；**不判质量** | DeepSeek、BGE、真实索引 | 不 Mock，手动/定时 | P1 |

### 6.10 Session 与多轮

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| SES-UNIT-001 | session | Unit | 创建、顺序、隔离 | 新建两个 Session，各 `add_turn` | 默认 `[]`；顺序 `user,assistant`；`len()` 正确；两实例互不影响 | 无 | 无 | P0 |
| SES-UNIT-002 | session | Unit | 公开可变引用语义 | `ctx = s.context(); ctx.append(...)`；改写 `ctx[0]["content"]` | 现状：**直接改变 Session 内部状态** → 测死该风险语义，不写成"隔离保证" | 无 | 无 | P1 |
| SES-UNIT-003 | session | Unit | 空/异形输入 | `add_turn("", "")`；`add_turn(None, None)` | 现状：照单写入，不做校验 → 记录契约边界 | 无 | 无 | P2 |
| SES-INT-001 | multi-turn | Integration | 上下文传递 + 原话写回 | 两轮复用同一 Session，第二轮为省略追问 | 第二轮 `rewrite` 收到 2 条历史；生成侧 history 为纯文本；最终 4 条消息且第 3 条是**原追问**而非 standalone | pipeline | scripted fake | P0 |
| SES-INT-002 | multi-turn | Integration | 换话题不被拽回 | 先 opamp 再问 feedback | 第 2 轮 Router 收 standalone；走 RAG 分支；历史顺序不变 | pipeline | scripted fake | P1 |
| SES-INT-003 | multi-turn | Integration | 失败轮次不产生半条历史 | 第 1 轮成功、第 2 轮 `ask_rag` 抛错 | 第 2 轮后 `len(session)` 仍为 2，历史无半轮 | pipeline、session | patch 能力函数 | P0 |
| SES-INT-004 | multi-turn | Integration | 长历史无窗口 | 连续 10 轮 | 现状：`context()` 返回全部 20 条，无截断 → 记录 token 增长风险与后续窗口需求 | pipeline | fake | P2 |
| SES-E2E-001 | multi-turn | E2E | 三轮闭环 | 与 `demo_multi_turn.py` 同剧本 | `0→2→4→6`；第 2 轮参数保真；第 3 轮类型切换为 `feedback` | 全链 | scripted fake，真实 Session | P0 |
| SES-RISK-001 | session | Integration | 并发非安全观测 | 2 线程共享一个 Session 各写 50 轮 | 不设"通过"门槛；若观察到条数 <100 或消息错序即记录；标 `slow`，不进 CI | 线程调度 | 受控实验 | P2 |
| MTR-AI-001 | multi-turn | AI Eval | 上下文理解质量 | 指代/参数替换/纠错/换题/长历史剧本 | standalone 保真率、路由准确率、答案是否用新参数、旧数值污染率 | 真实 LLM | 不 Mock；逐轮存档 | P1 |

### 6.11 Judge 与评测脚本

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| JUD-UNIT-001 | judge | Unit | 无 tool_call / 坏 JSON | 两份异常响应 | `judge_answer` 默认 `partial`；`judge_hallucination` 默认 `False`；reason 可识别 | 无 | patch `aurora.judge.get_client` | P0 |
| JUD-UNIT-002 | judge | Unit | 非法枚举/类型收敛 | `judgment="maybe"`；`hallucinated="yes"`；`hallucinated=1` | 非法枚举 → `partial`；非 `bool` → `False` | 无 | fake | P0 |
| JUD-UNIT-003 | judge | Unit | 字段回填契约 | 正常 `{"judgment":"correct","reason":"…"}` | 返回 `{"judgment": "correct", "reason": "…"}`；幻觉分支返回键为 `hallucinated` 而非 `judgment` | 无 | fake | P1 |
| EVL-UNIT-001 | eval.py | Unit | 关键词判分边界 | `expected="5V\|5 V"`；答案含"不是 5V"；`answer=None` | 现状：任一子串命中即 True（含否定句假阳性）；`None` 不抛异常返回 False | 无 | 直接导入 `scripts.eval.is_correct` | P1 |
| EVL-UNIT-002 | eval_report.py | Unit | 引用率/多数投票 | 重复 source、空 hits、投票 2:1 与 1:1 | 命中率 = 命中来源数/片段数（重复来源会重复计数）；`hits=[]` → `None`；平票取先出现值 | 无 | 无 | P1 |
| EVL-UNIT-003 | eval_report.py | Unit | 汇总分母 | 12 题中 7 道有 hits | 引用率与幻觉率分母为 RAG 题数（7），不是总题数；无 RAG 题时打印"无 RAG 题"且不除零 | 无 | 构造 rows | P1 |
| EVL-META-001 | judge | AI Eval（元） | 裁判与人工一致性 | 人工双标 40 条（正确/部分/错误/幻觉） | 报告 agreement、Cohen's κ、各类 precision/recall；同源模型偏差单列 | 裁判 LLM + 人审 | 盲审 + 异源裁判抽检 | P0 |

### 6.12 建库脚本与旧入口

| Test ID | Module | Level | Objective | Input / Scenario | Expected Result | Dependencies | Mock Strategy | Priority |
|---|---|---|---|---|---|---|---|---|
| IDX-INT-001 | build_index | Integration | 可控语料建库 | `tmp_path` 下 2 个 md + 1 个卡片文件 + 假 encoder | Chunk 总数 = 课件块 + 卡片块；落盘两个文件；自检 `top[0].score ≈ 1`；**不写真实索引** | 文件系统 | patch 路径常量 + `encode_passages` | P1 |
| IDX-INT-002 | build_index | Integration | 空语料与缺失卡片 | courseware 目录为空；卡片文件不存在 | 空目录 → 打印提示并 `return 1`；缺卡片 → 跳过并继续 | 文件系统 | `tmp_path` | P1 |
| LGC-UNIT-001 | companion_v0 | Unit | 消息构造 | `ask("q", history=[...])` | messages 为 `system→history→user`；`temperature=0.7` | 无 | patch `aurora.companion_v0.get_client` | P2 |
| LGC-E2E-001 | chat_loop | E2E | 旧入口冒烟 | 单轮输入 + `exit` | 能正常退出；不涉及 Pipeline（明确记录"两条入口"现状） | 真实 LLM | 自动化需 patch `builtins.input` 与 `ask_stream`；当前仅有手工入口 | P2 |

---

## 7. 外部依赖与 Mock 策略

各类外部依赖（LLM、Embedding/BGE、VectorStore、文件系统、环境变量、网络）的默认策略、打补丁位置与禁止事项见 `TESTING_GUIDE.md` §7「外部依赖隔离」与 §9.1「文件隔离」；三层防线的分工见同一文件 §8；统一 Fake 的实现规范见同一文件 §6。本节不重复这些表格。

测试侧只需记住三条结论：

1. 默认执行下所有模型调用都是 Fake，没有例外。
2. 唯一的"真实"依赖是内存 `VectorStore` 与 `tmp_path`，它们本身就是被测对象。
3. 时间与随机不做打桩：`eval_report` 的延迟只做冒烟，不进断言阈值，避免机器差异导致伪失败。

---

## 8. 测试数据与 Fixture 设计

目录结构、文件命名与 Fixture 职责见 `TESTING_GUIDE.md` §3 与 §4，本节只规定数据本身的设计要求。

数据设计要点：

1. **微型向量集**：`[[1,0],[0,1],[0.6,0.8],[0,1]]` 可覆盖"降序""同分""k>N""空库""负 k"，且分数可用手算核对。

2. **微型知识库**：3 个 Chunk（含 1 个最高分、1 个无关），断言检索排序与 `build_context` 编号，不加载真实索引。

3. **畸形响应矩阵**：`tool_calls=[]`、`arguments="{oops"`、`arguments="[]"`、`arguments="null"`、`confidence="high"`、`confidence=null`、`content=None`、`finish_reason="length"`。**这 8 种是 Router/Agent 崩溃的高发点**，应集中在一个参数化用例里。

4. **文本边界样本**：全角数字标题、全角单位、PUA 码点、表格竖线、占位文案、纯公式行。

5. **AI Eval 黄金集**（独立于 `tests/`）：每条至少含 `id / capability / difficulty / expected_type / gold_sources / acceptable_answers / forbidden_facts / turn_relation / reviewer`。现有题库规模只能当 smoke seed，不能当泛化集；题量随数据集版本变化，不写进本文。

6. **数据切分**：同一知识点的同义改写不得同时进开发集与报告集，否则指标虚高。

---

## 9. 现有脚本的覆盖与局限

- `eval.py`：跑完整 Pipeline 的在线 happy path；答案判分是"任一关键词子串命中"，**否定句也会被判对**（如期望 `5V`、回答"不是 5V" → 判对）。

- `eval_report.py`：多次采样 + 多数投票 + LLM judge + 幻觉 + 引用命中率 + 延迟；但考生与裁判同为 DeepSeek（同源偏差），报告只打印到终端，**不保存模型版本/索引 hash/token/费用**，无历史基线。

- `demo_multi_turn.py`：只断言"历史长度 6"，**不能**证明改写正确、分支正确、答案用了新参数。

- `build_index.py` 的自检不断言分数，分数低也不影响退出码。

- 两个评测脚本强依赖网络与本地模型，**不适合默认 CI**。

## 10. 测试覆盖缺口（按风险排序）

| # | 缺口 | 后果 | 证据 | 优先级 |
|---|---|---|---|---|


| 1 | RAG `content=None`、`run_agent()` 失败出口被当成功（轮数耗尽 / 截断但有内容） | 失败结果被当成功写进 `Session` 历史，污染下一轮语义 | `rag.ask_rag` 的 `content=None` 分支、`agent.py:120-130` 的三个出口；决策见 §13 第 2、3 条 | P0 |
| 2 | Router 非 dict JSON / 非数值 confidence 不兜底 | 第三方响应形状一变即 `AttributeError`/`TypeError` | `router.route` 的 `arguments` 解析段 | P0 |
| 3 | validator 对每个中间计算都生效 | 中间量 >15 即误报"运放饱和"，诱导模型改错结论 | `agent.run_agent` 的 `validator` 挂载点 | P0 |
| 4 | 五分支与失败写回无回归 | 分支错配、半轮历史污染不可见 | 缺少覆盖五分支与失败写回的回归用例 | P0 |
| 5 | `execute_tool` 结果只保留 6 位有效数字 | `round(x, ndigits=2)` 一类调用得不到预期精度，而提示词要求模型“直接引用工具结果”→ 截断值被当标准答案写进回答 | `tools.calculate_expression` 的结果格式化分支 | P1 |
| 6 | 向量与 chunk 数量/维度无校验 | 索引损坏时错配命中或深层异常 | `VectorStore.search`、`VectorStore.save/load` | P1 |
| 7 | 无相似度阈值、无"无答案"标注 | 无关问题也硬塞片段，幻觉入口 | `rag.ask_rag` 的 `if not hits` 分支（只判空、不判分数） | P0（AI Eval） |
| 8 | 评测集规模过小且模板化 | 指标不代表泛化，满分通过也无统计意义 | `data/eval_set.json` | P0 |
| 9 | Session 返回内部可变列表 + 无限增长 | 状态可被绕过写接口修改；长会话 token 与费用线性增长 | `Session.context`、`Session.add_turn` | P1 |
| 10 | 超时/重试依赖 SDK 默认 | 未显式配置，行为随 `openai` 版本浮动，不是项目契约 | `config.get_client` 未传 `timeout` / `max_retries` | P1 |
| 11 | 索引不可复现 | 课件在仓库外、`data/index` 被忽略，他机无法重建同结果 | `.gitignore`、`build_index.py` 的 `COURSEWARE_DIR` 常量 | P1 |
| 12 | 评测不留档 | 无法做质量/成本/延迟回归 | `eval_report.py` 只 `print` | P1 |
| 13 | 全角字符被清洗 | `输出＝１５Ｖ` → `输出正常`，知识文本丢关键数值 | `chunker._clean_line` | P1（需重跑索引） |
| 14 | `resp.choices` 为空列表时直接取 `resp.choices[0]` | 未处理的响应形状 → 裸 `IndexError`，非「失败被当成功」类，但同样无降级 | `agent.run_agent` 的 `choice = resp.choices[0]` | P1 |
| 15 | `finish_reason="length"` 同时带 `tool_calls` 时，`if msg.tool_calls` 先命中并继续执行工具 | 截断被当正常，多算一轮后仍可能返回半截结果（概率极低，与 #14 同属响应形状防御） | `agent.run_agent` 的 `if msg.tool_calls` 分支位置 | P2 |

---

## 11. 实施优先级与阶段安排

### 阶段 0：基础设施（0.5 天）

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

### 阶段 1：纯逻辑 P0（1.5 天）

`tools`（TOL-UNIT-001/002/003/005/006/007/009）、`chunker`（CHK-UNIT-001/002）、`card_loader`（CRD-UNIT-001）、`vector_store`（VST-UNIT-001/002/003、VST-INT-001）、`session`（SES-UNIT-001）、`config`（CFG-UNIT-001）。

其中 TOL-UNIT-003 断言关键字参数被显式拒绝、位置参数仍正确。

补充加固：`TOL-UNIT-010`（P2、`slow`）单独执行，不作为阶段 1 P0 准入项，但阶段验收必须显式报告其执行状态。

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

不要先跑在线评测代替基础回归：`eval_report.py` 一次全量运行就要数百次付费调用（每题 3 采样 × 3~4 次调用），却无法定位任何确定性缺陷。

---

## 12. CI 建设建议（GitHub Actions）

**每个 PR 必跑（离线，<3 分钟）**：

```yaml
- uv sync --frozen --dev          # 锁文件必须一致
- uv run pytest -q                # 默认排除 online/ai_eval/slow
- git diff --check                # 空白/冲突标记
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

## 13. 产品契约决策与剩余风险

> 第 1–4、13 条已于 2026-11-22 定案（保留决策依据）；第 5–12 条仍待确认。

1. **`store=None` 且路由到 RAG 分支**：继续抛 `AttributeError`（视为开发错误），还是返回可识别降级文案？无论哪种，都不应直接调 `None.search()`。

   **已定案**：显式抛 `ValueError`（消息含 `VectorStore`），不调用 `None.search`，Session 不写回；缺陷 DEF-05，断言见 §6.6 `RAG-INT-004`。

2. **RAG `content=None`**：抛异常、返回空串、还是降级文案？当前会写入历史并污染下一轮。

   **已定案**：抛 `RuntimeError("模型没有返回有效内容")`，在写回前中断，Session 长度不变；缺陷 DEF-06，断言见 §6.6 `RAG-INT-005`。

3. **`run_agent()` 失败路径契约**：同一函数现有三个出口语义不一致，需统一定调——(a) 达 `max_rounds` 返回占位文案（`agent.py:130`）、`trace` 正常返回，`pipeline.run()` 读不到区别；(b) `finish_reason="length"` 但已生成部分内容时按正常回答返回（`elif msg.content` 先于 `finish_reason` 判断），半截答案被当成功；(c) 无内容 / 空响应抛 `RuntimeError`，属无降级的硬失败。

   **已定案**：采用方案①——(a) 与 (b) 统一抛 `RuntimeError`（(a) 消息含「最大工具调用轮数」，(b) 复用「模型输出达到长度限制」），`pipeline.run()` 因异常在写回前而不写入历史；缺陷 DEF-07、DEF-08，断言见 §6.8 `AGT-UNIT-004`、§6.9 `PIP-INT-005`。以下为决策时的候选与代价分析，保留备查。候选方案：① 统一抛明确异常（`(str, list)` 契约不变，但调用方需处理新异常：`pipeline.py:132`、`scripts/demo_tools.py:48`，以及经 `pipeline.run()` 拿 trace 的 `demo_router.py:41-46`、`demo_multi_turn.py:57-62`）；② 返回值携带结构化状态（影响同上，且改动更大）；③ 保留 `(str, list)`，在 `trace` 末尾追加终止标记（如 `{"status": "max_rounds_exhausted"}`），`pipeline.run()` 读到标记后再决定是否 `add_turn()`。③ 免改签名，但仍要动六个消费点：`agent.py:130` 追加标记；`pipeline.py:132` 起读取标记并决定写回；`scripts/demo_tools.py:52-56`、`demo_router.py:43-46`、`demo_multi_turn.py:59-62` 三处同模式，`step['tool']` / `step["warning"]` 需改 `.get()`（标记条目无这些键，会 `KeyError`）；`tests/integration/test_agent_tools.py:35-42` 的 trace 精确等值断言（多一条即变红），并同步 `AGT-UNIT-004` 期望。③ 与 ① 都能覆盖 (a)(b)，区别在代价性质：① 把「半截答案」换成异常，用户拿不到已算出的部分结果，且四处调用方都要加 `try/except`；③ 保留可读结果、由下游识别标记，改动更多但都是机械的 `.get()` 与分支。

4. **validator 语义**：只校验"最终输出"需要标识最终值；当前按工具名挂载导致中间量误报。是加"最终值"标记、拆专用工具，还是取消自动电压校验？仅改文案不解决问题。

   **已定案**：暂按现状锁定——保留「每次 `calculate_expression` 都校验」，作为已知风险记录，不写成成功契约；断言见 §6.8 `AGT-INT-002`。改造方案另立任务。

5. **`execute_tool` 精度**：`:g` 六位有效数字是否可接受？改成 `repr(value)` 会改变模型看到的字符串与既有 demo 输出。


6. **布尔字面量**：`True` 当前算作 `1`，是否收紧为非法？

7. **全角字符清洗**：修复会改变 Chunk 文本，必须重建索引并重跑检索基线；是否与"索引版本化"一起做？

8. **`context()` 返回内部列表**：接受为公开语义，还是改为副本？改副本是行为变更，需单独验收。

9. **超时与重试**：是否显式配置？当前未配置，行为随 `openai` 版本浮动（具体继承值不写进计划，需要时现场观测），显式化后才算项目契约。

10. **评测独立性**：是否引入异源裁判，或"规则 + 人工盲审 + DeepSeek"组合？同源偏差目前无量化。

11. **评测集授权**：黄金集能否使用课件衍生内容？仓库外课件如何版本化？未定前检索指标不可完全复现。

12. **质量门槛**：在拿到首轮可信基线前，本文不设任何百分比；由你决定是"只看趋势"还是"设回归阈值"。

13. **空索引降级文案是否写入历史**：空库时 `ask_rag()` 返回"课件库还是空的…"，Pipeline 会把它当正常答案 `add_turn`。

    **已定案（2026-11-22）**：保留写入。该文案是用户实际看到的最终回答，写入历史可保持界面可见对话与 Session 一致；未来若引入结构化错误状态，再单独设计降级状态和写回规则。断言见 §6.9 `PIP-E2E-002`。

---

## 14. 本计划的完成标准

阶段 1 完成的标准不是"用例数量"，而是：**阶段 1 范围内的每个已知 P0 缺陷都有稳定复现或定标用例，并满足本阶段准入条件**。在此之前，通过率 100% 这类数字不构成质量证据。
