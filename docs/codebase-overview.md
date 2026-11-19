# Aurora 代码库现状说明

本文档描述 Aurora 当前代码已经实现的架构、运行链路和工程边界，面向需要阅读、维护或评估本项目的开发者。

结论以 `src/aurora/`、`scripts/`、`pyproject.toml` 和 `data/eval_set.json` 的实际代码与配置为依据。文中使用以下状态标记：

- **已实现**：当前代码中存在完整实现。
- **未实现**：当前仓库没有对应实现。
- **未知**：仅靠静态代码无法确认，需要实际环境或外部服务验证。

本文档是静态分析结果，不代表外部模型调用、索引重建或评测已经在当前环境中执行成功。

核对基线：commit `cf01520`（2026-11-18）。

## 1. 项目定位

Aurora 是一个面向《模拟电子技术》课程的命令行智能助教原型。它将用户问题分类后分派到不同处理链路：数值计算题进入 Tool Use，知识问答进入 RAG，超出课程范围的问题进入固定降级回答。

当前核心编排入口是 `aurora.pipeline.run()`。仓库没有 Web API、图形界面或持久化服务，完整 Pipeline 主要由 demo 和评测脚本调用。

需要注意，`scripts/chat_loop.py` 仍调用早期的 `aurora.companion_v0.ask_stream()`。该交互脚本只把历史消息直接发送给模型，不经过当前的上下文改写、Router、RAG、Tool Use 或 `Session`。因此，“命令行可交互聊天”和“完整智能体 Pipeline”目前是两条不同入口。

## 2. 技术栈

| 分类 | 当前实现 |
| --- | --- |
| 语言与构建 | Python `>=3.14`、`uv_build`、`uv.lock` |
| 生成模型 | DeepSeek OpenAI 兼容接口，默认模型 `deepseek-chat` |
| 模型 SDK | `openai` |
| 环境配置 | `python-dotenv` + 环境变量 |
| Embedding | `sentence-transformers` + `BAAI/bge-small-zh-v1.5` |
| 向量计算 | NumPy |
| 向量存储 | 自研内存矩阵，使用 `vectors.npy` 和 `chunks.json` 持久化 |
| Agent 编排 | 项目内同步 Python 函数，没有使用 LangChain 或 LangGraph |
| 评测 | 关键词匹配、LLM-as-judge、多次采样多数投票 |

## 3. 目录结构

```text
aurora/
├── src/aurora/             核心 Python 包（含 py.typed 类型标记）
├── scripts/                建库、演示、交互与评测入口
├── knowledge/              人工整理的公式卡片
│   └── pages/              14 张教材切图（Git 忽略）
├── data/                   评测集和本地生成的向量索引
├── docs/                   架构图与项目说明
├── .env.example            DeepSeek 配置示例
├── .python-version         本地 Python 版本声明
├── pyproject.toml          包元数据、Python 版本和直接依赖
└── uv.lock                 依赖锁文件
```

各目录的实际职责如下：

- `src/aurora/`：包含配置、LLM 调用、Router、RAG、Tool Agent、Session、问题改写和评测裁判。
- `scripts/`：全部可直接执行的入口。项目没有在 `pyproject.toml` 中注册 console script。
- `knowledge/`：`formula_cards.md` 保存 8 张人工公式卡片，建库时与课件 Chunk 混合写入同一个索引；`pages/` 当前保存 14 张教材切图并被 Git 忽略。
- `data/`：`eval_set.json` 保存 12 条评测用例；`data/index/` 是可重建且被 Git 忽略的索引产物。
- `docs/`：不参与运行时调用。
- `../courseware_md/`：位于仓库外部的课件 Markdown 目录，是 `scripts/build_index.py` 的输入依赖。

## 4. 模块地图

### 4.1 编排与模型调用

| 模块 | 主要接口 | 职责 |
| --- | --- | --- |
| `pipeline.py` | `run()` | 统一编排：问题改写、分类、分派、Session 写回和结果汇总 |
| `rewrite.py` | `rewrite_question()` | 根据最近两轮历史，将省略式追问改写为独立问题 |
| `router.py` | `route()` | 通过强制 function calling 返回问题类型 |
| `agent.py` | `run_agent()` | 执行模型与本地工具之间的多轮调用回环 |
| `rag.py` | `ask_rag()` | 检索、拼接上下文并调用模型生成回答 |
| `companion_v0.py` | `ask()`、`ask_stream()` | 早期直连模型的单轮/流式聊天能力 |
| `config.py` | `get_client()` | 读取 DeepSeek 配置并创建 OpenAI 兼容客户端 |

### 4.2 知识库与检索

| 模块 | 主要接口 | 职责 |
| --- | --- | --- |
| `chunker.py` | `chunk_text()`、`chunk_file()` | 清洗课件文本并按约 600 个中文字符切块 |
| `card_loader.py` | `load_cards()` | 将 Markdown 公式卡片解析为 `Chunk` |
| `embedder.py` | `encode_passages()`、`encode_query()` | 使用同一 BGE 模型编码文档和查询 |
| `vector_store.py` | `VectorStore` | 保存 Chunk 与向量，通过点积完成 top-k 检索；`add()` 当前没有调用方 |

### 4.3 工具与评测

| 模块 | 主要接口 | 职责 |
| --- | --- | --- |
| `tools.py` | `calculate_expression()`、`execute_tool()` | 使用 AST 白名单计算数学表达式并分派工具 |
| `tools.py` | `validate_opamp_output()` | 检查运放计算结果是否超过电源电压 |
| `session.py` | `Session` | 保存进程内多轮消息历史 |
| `judge.py` | `judge_answer()`、`judge_hallucination()` | 使用结构化 LLM 调用评估答案和幻觉 |

## 5. 完整问题处理链路

完整 Pipeline 的调用关系如下：

```text
demo / eval 脚本
    │
    ▼
pipeline.run(question, store, session)
    │
    ├─ Session.context() 读取历史
    ├─ rewrite_question() 生成 standalone question
    ├─ router.route() 返回 circuit_type / confidence / reason
    │
    ├─ opamp ──────► run_agent() ─► execute_tool() ─► 物理校验 ─► 最终回答
    ├─ feedback ───► ask_rag()，使用决策树提示词
    ├─ power ──────► ask_rag()，使用链式流程提示词
    ├─ concept ────► ask_rag()，使用默认 RAG 提示词
    └─ fallback ───► fallback_answer()，不检索、不调用生成模型
    │
    ├─ Session.add_turn() 写入原始问题和最终回答
    ▼
统一结果 dict
```

### 5.1 输入与上下文

`pipeline.run()` 接收：

- `question: str`：用户本轮原始问题。
- `store: VectorStore | None`：RAG 使用的向量库。
- `top_k: int`：检索返回数量，默认 3。
- `session: Session | None`：可选多轮状态，仅允许关键字传参。

未传 `Session` 时，函数会创建临时 Session。因此默认调用仍是单轮行为。

### 5.2 上下文改写

`rewrite_question()` 在首轮直接返回原问题，不调用模型。有历史时，它只读取最近两轮，每条消息最多保留 300 个字符，然后调用 DeepSeek 生成不依赖上文的 `standalone` 问题。

改写结果只用于 Router 和 RAG 查询，不进入生成侧，也不写入 Session。生成回答仍使用原始问题和真实历史，避免改写过程中发生的数值或条件偏差污染最终答案。

`opamp` 分支同样会执行改写，但 `standalone` 只供 Router 分类；该分支不参与检索，因此这次改写不会被后续查询消费，属于纯额外调用开销。

### 5.3 Router

`route()` 使用 `tool_choice` 强制模型调用 `route_circuit`，返回以下五种标签之一：

| 标签 | 处理链路 |
| --- | --- |
| `opamp` | Tool Agent + 运放输出校验 |
| `feedback` | RAG + 负反馈决策树提示词 |
| `power` | RAG + 稳压电源链式流程提示词 |
| `concept` | RAG + 默认学伴提示词 |
| `fallback` | 固定降级回答 |

Router 输出是普通字典：

```python
{
    "circuit_type": "concept",
    "confidence": 0.0,
    "reason": "...",
}
```

模型未返回 tool call、JSON 无法解析或标签不在枚举中时，Router 回退到 `concept`。代码没有根据 `confidence` 设置二次确认或降级阈值。

### 5.4 分支执行与统一收口

`opamp` 分支将原问题和历史交给 `run_agent()`。其余受支持的知识类问题进入 `ask_rag()`；`fallback` 直接生成模板文本。

所有分支成功结束后都会经过同一个收口点：先调用 `Session.add_turn()`，再返回统一结果。

```python
{
    "circuit_type": str,
    "confidence": float,
    "reason": str,
    "standalone": str,
    "answer": str,
    "trace": list[dict] | None,
    "hits": list[SearchResult] | None,
}
```

## 6. RAG 实现

### 6.1 离线建库

`scripts/build_index.py` 完成以下步骤：

1. 从仓库上一级的 `courseware_md/*.md` 读取课件。
2. `chunk_file()` 丢弃所有码点大于等于 `U+E000` 的字符，替换表格竖线，并过滤 PPT 占位行。这个范围不仅包含 Unicode 私有区，也会删除落在该码点范围内的全角标点、全角数字和全角运算符。例如 `输出＝１５Ｖ，正常` 会被清洗为 `输出正常`。
3. 只保留含汉字的正文，并按约 600 字封装为 `Chunk`。
4. `load_cards()` 将 `knowledge/formula_cards.md` 中每个二级标题解析成一个额外 Chunk。
5. `encode_passages()` 批量生成归一化向量。
6. `VectorStore.save()` 写入 `data/index/vectors.npy` 和 `chunks.json`。
7. 使用第一块文本进行一次自身检索并打印相似度。脚本不对分数做断言，分数高低也不影响退出码。

当前本地索引包含 24 个 Chunk，但该目录被 Git 忽略，不能视为仓库可交付内容。

### 6.2 在线检索与生成

`ask_rag()` 的数据流为：

```text
standalone query
  → encode_query() 添加 BGE 查询前缀并向量化
  → VectorStore.search() 与全部向量做点积
  → 取分数最高的 top-k 个 SearchResult
  → build_context() 拼接来源和正文
  → 原始问题 + 历史 + 检索上下文发送给 DeepSeek
  → 返回 answer 和 hits
```

文档向量和查询向量均做归一化，因此 `VectorStore.search()` 直接使用矩阵点积作为余弦相似度。

当前 RAG **未实现**以下能力：

- 相似度最低阈值。
- 关键词与向量混合检索。
- rerank。
- metadata filter。
- 来源权限或可信等级。
- 对知识文本中提示注入内容的隔离。

因此，只要索引非空，系统就会返回 top-k 片段，即使最高分仍然较低。

## 7. Tool Use 与校验回环

### 7.1 工具定义

当前 `TOOLS` 只包含 `calculate_expression`。工具 schema 要求一个字符串参数 `expr`，支持基本四则运算、幂、取模、常量 `pi/e` 和有限的数学函数。

### 7.2 安全执行

`calculate_expression()` 不使用 `eval()`，而是通过 `ast.parse(..., mode="eval")` 解析表达式，再由 `_eval_node()` 递归放行白名单节点。

以下输入会被拒绝：属性访问、下标、列表和字典、任意函数名、变量赋值及其他未显式允许的 AST 节点。

`execute_tool()` 负责名称分派、参数检查和部分异常转换。`ValueError`、`ZeroDivisionError` 和 `OverflowError` 会转换成字符串结果，再交还模型处理；`ast.parse()` 产生的 `SyntaxError` 不在捕获范围内，会向上抛出并中断 Pipeline。

`_eval_node()` 执行函数调用时只处理 `node.args`，没有检查或传递 `node.keywords`。关键字参数因此会被静默丢弃，可能产生错误数值。例如 `round(3.14159, ndigits=2)` 实际按 `round(3.14159)` 执行，结果为 `3.0`。

### 7.3 Agent 回环

`run_agent()` 最多执行 5 轮：

1. 发送 `messages` 和工具 schema。
2. 模型返回一个或多个 tool call。
3. 代码解析每个工具参数并执行本地函数。
4. 可选 validator 检查工具结果。
5. 工具结果和校验警告以 `tool` 消息发回模型。
6. 模型继续调用工具，或返回最终文本。

每次工具调用都会记录为 trace：

```python
{
    "tool": "calculate_expression",
    "arguments": {"expr": "..."},
    "result": "5",
    "warning": None,
}
```

### 7.4 运放物理校验

Pipeline 只在 `opamp` 分支为计算器挂载 `validate_opamp_output()`。该函数将工具结果解析为浮点数，检查绝对值是否超过 `SUPPLY_VOLTAGE`，超过时生成饱和与钳位警告。

当前 `SUPPLY_VOLTAGE` 固定为 `15.0`，不会从用户问题中提取实际电源电压。这是当前行为边界，不是动态电路参数。

## 8. Session 与多轮状态

`Session` 是一个只包含 `history` 的 dataclass：

```python
@dataclass
class Session:
    history: list[dict] = field(default_factory=list)
```

`add_turn()` 按 `user`、`assistant` 顺序成对追加消息；`context()` 原样返回完整历史。长期历史只保存自然语言问答，不保存 Tool Agent 内部的 `tool_calls` 和 `tool` 消息。

当前状态管理具有以下边界：

- 只存在于当前 Python 进程内。
- 没有 session ID 或用户 ID。
- 没有数据库、文件持久化或恢复机制。
- 没有并发控制、锁或跨进程共享。
- 没有过期机制、窗口截断、摘要压缩或 token 预算。
- `context()` 返回内部列表本身，不是副本；调用方可以绕过 `add_turn()` 直接修改状态。

`scripts/demo_multi_turn.py` 使用同一个 Session 连续执行三轮，并通过 `len(session) == 6` 检查问答是否成对写回。这是演示脚本中的运行时断言，不是独立自动化测试。

## 9. 数据结构与接口约定

### 9.1 消息

模型消息遵循 OpenAI 兼容格式：

```python
{"role": "user", "content": "..."}
{"role": "assistant", "content": "..."}
{"role": "system", "content": "..."}
{"role": "tool", "tool_call_id": "...", "content": "..."}
```

约定上，Session 只写入 `user` 和 `assistant` 的纯文本消息；`Session` 本身不校验 `role` 或消息结构。

### 9.2 检索对象

`Chunk` 包含：

- `text: str`：正文。
- `source: str`：来源文件名或公式卡片名称。
- `heading: str`：最近识别到的标题。

`heading` 由“清洗后不超过 30 个字符且首字符为数字”的启发式规则识别，可能把普通短句误判为标题，不可作为可靠定位信息。

`SearchResult` 包含：

- `chunk: Chunk`。
- `score: float`。

`VectorStore` 依靠位置约定关联 `chunks[i]` 与 `vectors[i]`，代码没有额外校验两者数量和维度是否一致。

### 9.3 契约强度

除 `Chunk`、`SearchResult` 和 `Session` 外，Router、Pipeline、trace 和评测结果主要使用普通字典。当前没有 `TypedDict`、Pydantic 模型、运行时 schema 校验或接口版本字段。

## 10. 配置管理

`config.py` 在模块导入时执行 `load_dotenv()`，然后读取：

- `DEEPSEEK_API_KEY`：必填。
- `DEEPSEEK_BASE_URL`：默认 `https://api.deepseek.com`。
- `DEEPSEEK_MODEL`：默认 `deepseek-chat`。

缺少 API key 时，`get_client()` 抛出 `RuntimeError`。`.env` 已加入 `.gitignore`，仓库只保留 `.env.example`。

环境变量值在模块导入时确定，同一进程内后续修改环境变量不会自动刷新这些模块常量。`get_client()` 每次调用都会创建新的 `OpenAI` 实例，并未缓存或复用客户端。

## 11. 异常处理与日志

当前异常处理是各模块局部处理，没有统一异常层：

- `config.py`：缺少 key 时主动失败。
- `router.py`：无 tool call、JSON 解析失败和非法标签时回退到 `concept`。
- `agent.py`：非法工具 JSON 按空参数执行；模型输出被截断或为空时抛出 `RuntimeError`。
- `tools.py`：将 `ValueError`、`ZeroDivisionError` 和 `OverflowError` 转换为可返回给模型的错误字符串；`SyntaxError` 会继续向上抛出。
- `rewrite.py`：异常、空结果或超过 500 字时回退原问题；正常结果会剥离首尾空白和引号。
- `rag.py`：空索引时返回提示文本。
- `judge.py`：结构化参数无法解析时返回默认评测结果。

除问题改写层外，网络异常、鉴权失败、限流、超时、响应结构变化、索引文件缺失和 embedding 模型加载失败通常会直接向上传播。项目没有统一重试、超时、熔断、错误码或用户级错误响应。

`rewrite_question()` 用 `try/except Exception` 兜底，任何异常、空结果或超过 500 字均回退原问题（`rewrite.py:98-119`，commit `cf01520`）。

日志系统目前**未实现**。源码没有使用 `logging` 或结构化日志；所有脚本通过 `print()` 输出过程信息，也没有 trace ID、请求 ID、日志级别或持久化指标。

## 12. 测试与评测现状

仓库已建立 pytest 阶段 0 基础设施：默认离线 marker 过滤、OpenAI 客户端/Socket/BGE 三层防线、严格脚本 Fake、微型合成数据及基础设施自检。pytest 9.2.0 已作为开发依赖安装并锁定；阶段 1 及之后的业务单元、集成和 E2E 测试以及 CI 仍未建立。执行证据以 `docs/testing/TEST_STATUS.md` 为准。

### 12.1 快速评测

`scripts/eval.py` 遍历 `data/eval_set.json`：

- Router 预测标签与标注字符串相等，视为分类正确。
- 最终回答包含任一期望关键词，视为答案正确。

关键词命中是启发式判断，不能验证完整推理或排除包含关键词的错误答案。

### 12.2 LLM 评测报告

`scripts/eval_report.py` 提供：

- 多次采样。
- 分类结果多数投票。
- LLM-as-judge 的 `correct/partial/wrong` 判分。
- RAG 回答幻觉判断。
- 引用命中率。
- Pipeline 延迟。

引用命中率与幻觉率仅对实际返回检索结果的 RAG 题统计，汇总分母是 RAG 题数，不是全部评测题数。

生成模型与裁判默认复用同一个 DeepSeek 模型，因此评测结论存在同源模型偏差。评测还依赖真实 API、可加载的本地 embedding 模型和已构建索引，结果不具备完全离线和确定性。脚本只将报告打印到终端，当前仓库没有保存历史评测报告产物。

### 12.3 当前测试结论

- 基础设施自检：**已实现**。
- 业务单元测试：**未实现**。
- 模块集成测试：**未实现**。
- CI 自动执行：**未实现**。
- 在线脚本评测：**已实现**。
- 当前测试通过率：**未知**，本文档生成过程中没有执行外部模型评测。

## 13. 已知技术债务与风险

本节仅记录当前实现事实及其影响，不给出改造方案。

### 13.1 入口与能力不一致

完整 Pipeline 只在 `demo_router.py`、`demo_multi_turn.py`、`eval.py` 和 `eval_report.py` 中使用。面向交互的 `chat_loop.py` 仍走 `companion_v0`，用户从该入口无法使用当前架构的分类、检索、工具和结构化 trace。

### 13.2 可选参数与实际前置条件不一致

`pipeline.run()` 将 `store` 标注为 `VectorStore | None`，但 `feedback`、`power` 或 `concept` 分支会把它直接传入 `ask_rag()`。当 `store=None` 且 Router 选择 RAG 分支时，检索会访问 `None.search()` 并失败。

### 13.3 外部调用的可靠性边界

一次多轮 RAG 请求可能依次发生“问题改写、Router、答案生成”三次同步 LLM 调用。代码没有设置显式超时、重试、并发限制或缓存。问题改写失败会回退原问题并继续执行；Router 或答案生成失败仍会中断整次 Pipeline，且本轮不会写入 Session。

### 13.4 状态与上下文增长

Session 将完整历史发送给生成链。随着轮数增加，请求 token、费用和延迟会持续增长，并可能触发模型上下文上限。

### 13.5 检索质量边界

向量检索没有最低相关性阈值，并且索引规模较小时仍固定返回 top-k。无关问题可能得到相关性较弱的课件片段，最终是否拒答完全依赖提示词和生成模型。

### 13.6 工具资源消耗

AST 白名单阻止了任意 Python 代码执行，但没有限制表达式长度、AST 深度、幂指数大小或计算时间。超大整数幂没有上界，可能耗尽内存或导致进程被系统终止；`execute_tool()` 捕获的 `OverflowError` 主要覆盖可抛出该异常的数值运算，不能为任意精度整数幂提供资源上限。

### 13.7 物理参数固定

运放输出校验使用全局固定值 `SUPPLY_VOLTAGE = 15.0`。即使用户题目提供其他电源电压，validator 仍按 ±15V 判断。

### 13.8 接口缺乏运行时约束

多个跨模块接口使用普通字典和 `None` 约定，字段缺失、类型变化或第三方响应格式变化可能在较深调用层才暴露为异常。

### 13.9 建库可复现性

完整课件位于仓库外部的 `../courseware_md/`，生成索引和 `knowledge/pages/` 教材切图又被 Git 忽略。仅获取当前仓库的开发者无法保证重建出与现有本地索引完全一致的内容，也无法取得当前本地的 14 张教材切图。

### 13.10 评测覆盖与独立性

当前评测集只有 12 题，覆盖五个 Router 类型，但不足以代表边界表达、工具异常、检索噪声、长对话和外部服务故障。使用同一模型生成和裁判也会降低评测独立性。

## 14. 已实现、未实现与未知汇总

### 已实现

- LLM Router 五类分流。
- RAG 离线建库与在线检索生成。
- 单一数学计算工具及 function-calling 回环。
- 运放输出电压校验警告。
- 进程内多轮 Session。
- 多轮问题改写。
- 固定降级回答。
- 脚本式分类、答案、幻觉、引用和延迟评测。

### 未实现

- Web/API/GUI 产品入口。
- 完整 Pipeline 的流式输出。
- Session 持久化、隔离和并发管理。
- 日志、监控、统一异常响应和调用重试。
- 相似度阈值、混合检索和 rerank。
- 自动化单元测试、集成测试和 CI。
- 通用工具注册、外部工具和并行工具执行。

### 未知

- 当前 `.env` 中的 DeepSeek 配置是否有效。
- 当前 API 账户的可用性、限额和实际响应兼容性。
- 当前机器能否成功加载或重新下载 BGE 模型。
- 现有本地索引是否与当前仓库外课件完全同步。
- 当前评测脚本的实际通过率和性能数据。
