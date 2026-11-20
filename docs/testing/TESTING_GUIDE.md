# Aurora 测试实施规范
> 适用基线：Aurora 当前 Python 3.14、`uv`、DeepSeek OpenAI 兼容客户端、BGE Embedding 和内存 `VectorStore` 实现。
> 上位设计：[`TEST_PLAN.md`](./TEST_PLAN.md)。本规范只说明如何实施，不改变其中的测试范围、优先级、用例 ID、缺陷定标方式或阶段准入标准。
> 当前状态：本文是实施规范，不代表测试基础设施或任何测试用例已经完成，也不包含任何 `Passed` 结论。
## 1. 适用范围与强制原则
本规范适用于 Aurora 的 Unit、Integration、离线 E2E、在线 E2E 和 AI Evaluation。测试层级与用例范围以 `TEST_PLAN.md` 的矩阵和详细用例为准。
所有测试实施必须遵守以下规则：
1. 默认测试不得访问 DeepSeek 或其他真实 LLM API，不得加载或下载真实 BGE 模型。
2. 默认测试不得依赖外部网络、真实用户数据、生产环境数据、仓库外课件或本机已有 `data/index/`。
3. Mock、Fake 和 Stub 必须验证关键调用行为，不能只提供固定返回值。
4. 不得把未执行、未收集、被过滤或只完成编写的测试记录为 `Passed`。
5. 不得通过降低断言标准、随意增加 `skip`、删除测试或排除既定用例来制造通过结果。
6. 真实 API 测试和 AI Evaluation 必须通过 marker 和运行命令显式启用；普通 `uv run pytest -q` 必须保持离线。
7. 测试不得写入 `.env`、`data/index/`、`../courseware_md/` 或其他真实项目数据位置。
8. `scripts/demo_*.py` 是演示，`scripts/eval.py` 和 `scripts/eval_report.py` 是在线评测入口；它们不能替代隔离的自动化测试。
## 2. pytest 安装、配置与运行
### 2.1 安装与锁定
pytest 只作为开发依赖，由 `uv` 管理。当前 `pyproject.toml` 工作区版本已声明：
```toml
[dependency-groups]
dev = [
    "pytest>=9.2.0",
]
```
测试基础设施实施阶段应执行并提交锁文件变更：
```powershell
uv lock
uv sync --frozen --dev
```
pytest 9.2.0 当前已安装，并已纳入开发依赖和锁文件。提交前使用 `uv lock --check` 验证 `pyproject.toml` 与 `uv.lock` 一致。
### 2.2 pytest 配置
阶段 0 已在 `pyproject.toml` 中增加以下配置；是否有效仍以当前工作区的收集与执行记录为准：
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not online and not ai_eval and not slow'"
markers = [
    "online: accesses a real external API and requires explicit enablement",
    "ai_eval: runs non-deterministic AI capability evaluation",
    "slow: takes materially longer than the default offline suite",
]
```
不得在 `addopts` 中默认开启真实 API、真实模型或评测作业。marker 必须注册，禁止依赖未注册 marker 的警告继续运行。
### 2.3 本地命令
```powershell
# 默认离线回归：Unit + Integration + 离线 E2E
uv run pytest -q
# 确认用例可收集；未收集到用例时 pytest 返回码为 5，不算通过
uv run pytest --collect-only -q
# 按层级或文件定位失败
uv run pytest -q tests/unit
uv run pytest -q tests/integration
uv run pytest -q tests/e2e
uv run pytest -q tests/unit/test_tools.py
# 显式在线冒烟；-o 清除默认 marker 过滤，且必须提供有效 API key
uv run pytest -q -o addopts="" -m online
# 显式 AI Evaluation；不属于普通软件回归
uv run pytest -q -o addopts="" -m ai_eval
```
选择 `online` 或 `ai_eval` 只是第一道开关。测试基础设施不会自动读取 `.env`；标记 `online` 的用例必须由执行者通过进程环境变量显式提供 `DEEPSEEK_API_KEY`。缺少凭据时应在测试入口明确失败，不能跳过、回落为伪造成功，或读取开发者本机 `.env` 密钥来满足前置条件。仅设置 API key 不得让默认套件产生在线调用。
## 3. 测试目录、命名与用例 ID
实施目录遵循 `TEST_PLAN.md` 已确定的结构：
```text
tests/
├── conftest.py
├── test_infra.py
├── fixtures/
│   ├── vectors.py
│   ├── llm_responses.py
│   └── knowledge/
├── unit/
│   ├── test_tools.py
│   ├── test_router.py
│   └── test_<source_module>.py
├── integration/
│   ├── test_pipeline_branches.py
│   └── test_agent_tools.py
├── e2e/
│   ├── test_pipeline_offline.py
│   └── test_multi_turn.py
└── online/
    └── test_<capability>_online.py
```
- 测试文件统一使用 `test_<subject>.py`。Unit 文件原则上与 `src/aurora/<module>.py` 对应；跨模块文件按被验证的调用链命名。
- 测试函数使用 `test_<test_id_lowercase>__<behavior>()`，例如 `test_tol_unit_003__keyword_arguments_take_effect()`。双下划线前是计划中的稳定 ID，后面是可读行为。
- 参数化用例仍保留一个 Test ID，参数 `id=` 描述场景，例如 `id="confidence-null"`，不得为每个参数临时创造未登记的计划 ID。
- 用例 ID 必须直接取自 `TEST_PLAN.md`，格式为 `<模块>-<层级>-<三位序号>`，如 `RAG-INT-005`。新增范围或改变优先级必须先回到计划评审，不能只在测试文件中扩张。
- 测试函数的首行 docstring 或紧邻注释应写明完整 Test ID、目标和关联缺陷号；测试报告必须能从 node id 追溯到计划用例。
## 4. Fixture 与 `conftest.py`
### 4.1 Fixture 原则
- fixture 只负责可复用的测试准备与清理，不隐藏核心断言。
- 默认使用 function scope。只有不可变且构建成本明确较高的数据才允许 module/session scope，并须证明不会泄漏可变状态。
- 每个用例新建 `Session`、`VectorStore` 和 `ScriptedClient`；不得跨用例共享响应队列、history、调用记录或向量矩阵的可变引用。
- 工厂 fixture 优于预制的可变对象，例如提供 `scripted_client_factory`，而不是提供一个全局 client。
- `tmp_path` 负责所有文件系统用例；`monkeypatch` 负责环境、模块符号和边界替换，并依赖 pytest 自动回滚。
- fixture 名称描述能力或数据，例如 `scripted_client_factory`、`router_tool_call`、`tiny_vector_store`，不使用 `mock1`、`data2` 等无语义名称。
### 4.2 根 `conftest.py` 的职责
根 `tests/conftest.py` 只承担跨层通用基础设施：
1. 三层防线的 autouse fixture，以及在线/AI Eval 用例的受控放行。
2. `ScriptedClient` 工厂和 OpenAI 兼容响应、message、tool call 构造器。
3. 3 至 5 个可手算二维向量及对应 `Chunk` 的微型数据工厂。
4. `get_model.cache_clear()` 的前后清理，防止 BGE 缓存跨用例污染。
5. marker 与环境前置条件的统一校验。
模块专用准备应放在对应测试文件或局部 `conftest.py`，不要把业务场景全部堆进根文件。`conftest.py` 不得导入并加载真实索引、真实课件或真实模型，也不得在收集阶段读取 API key 后自动创建客户端。
## 5. Mock、Fake 与 Stub
### 5.1 选择原则
- **Stub**：只提供调用方所需的最小静态值，适合不关心交互的纯输入边界。
- **Fake**：实现一个可运行但受控的简化协议，适合 LLM 客户端、Embedding 模型和内存数据。
- **Mock/Spy**：记录并断言交互，适合验证调用次数、参数、消息顺序和分支未调用。
优先使用简单的 Python 对象、`SimpleNamespace`、闭包和 pytest `monkeypatch`，不为当前项目额外引入 mocking 框架。替身必须与调用方实际访问的 OpenAI 响应形状一致。
每个外部边界测试至少验证适用的关键行为：
- 调用次数，包含“不应调用”的零次断言；
- `model`、`temperature`、`tools`、`tool_choice` 等关键参数；
- `messages` 的 role、顺序和关键 content；
- query 与原问题是否传到正确下游；
- 异常是否传播或按当前契约降级；
- 输入的 history、Chunk 和 Session 是否未被意外原地修改。
补丁必须打在实际使用方。`router.py` 等模块通过 `from aurora.config import get_client` 绑定本地名字，因此注入 Fake 时应 patch `aurora.router.get_client`、`aurora.rag.get_client`等，而不是 patch `aurora.config.get_client`。同理，RAG 检索应 patch `aurora.rag.encode_query`，Agent 工具边界应 patch `aurora.agent.execute_tool`。
## 6. `ScriptedClient` 设计规范
`ScriptedClient` 是离线测试 OpenAI 兼容调用链的统一 Fake，放在 `tests/conftest.py`。它必须支持 `client.chat.completions.create(**kwargs)`，但不应模拟无关 SDK 功能。
### 6.1 响应序列
- 构造时接收有序响应或有序动作序列，内部使用独立 `deque`。
- 每次 `create()` 严格消费队首元素，保证 Router、改写、Agent 多轮工具调用和最终回答的顺序可验证。
- 队列耗尽后仍被调用必须立即抛 `AssertionError("意外的额外模型调用")`。
- 用例结束时必须通过 `assert_exhausted()` 验证队列为空，捕获“实际调用少于脚本预期”。
- 响应可为完整响应对象；动作也可为待抛异常，用于验证超时、协议错误或服务异常路径。
### 6.2 调用记录与参数校验
- `calls` 按顺序保存每次 kwargs 的独立快照，避免后续消息列表修改污染历史记录。
- 提供 `call_count`、`last_call` 和 `assert_exhausted()`；复杂业务断言仍留在测试函数中，避免 Fake 隐藏预期。
- `create()` 可接受每一步的参数校验回调。回调在返回响应前检查 `messages`、`tools`、`tool_choice`、`temperature` 和 `model`，失败直接抛 AssertionError。
- 对 `messages` 做语义断言，不依赖整个长提示词逐字相等；但协议字段、role 顺序、工具名和必须存在的提示规则应精确断言。
### 6.3 异常处理
- Script 中的异常必须原样抛出，不得被 Fake 吞掉或转换成成功响应。
- Fake 自身的配置错误使用 `AssertionError`，业务边界预期异常使用脚本中指定的异常类型。
- 禁止在队列为空时返回 `None` 或重复最后响应，这会掩盖额外调用。
- 禁止自动补齐 `choices`、`message`、`tool_calls` 等缺失字段；畸形响应测试必须能真实触发当前解析行为。
概念接口如下，正式实现可增加类型注解，但不得削弱上述约束：
```python
class ScriptedClient:
    def __init__(self, steps):
        self._steps = deque(steps)
        self.calls = []
    @property
    def chat(self):
        return self
    @property
    def completions(self):
        return self
    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self._steps:
            raise AssertionError("意外的额外模型调用")
        step = self._steps.popleft()
        if step.validator:
            step.validator(kwargs)
        if step.exception:
            raise step.exception
        return step.response
    def assert_exhausted(self):
        assert not self._steps, "预期的模型调用未发生"
```
## 7. 外部依赖隔离
### 7.1 LLM
- Unit、Integration 和离线 E2E 一律使用 `ScriptedClient` 或“调用即失败”的哨兵。
- Fake 注入到 `aurora.<使用模块>.get_client`，并断言完整关键请求参数。
- `aurora.config.OpenAI` 的全局拦截负责兜底，不能替代用例级调用断言。
- 只有 `online` 用例可以使用真实 `get_client()`；AI Evaluation 需要真实客户端时必须同时标记 `online` 和 `ai_eval`，且默认 marker 过滤和凭据检查必须同时满足。
### 7.2 Embedding / BGE
- `aurora.embedder` 的调用契约测试 patch `aurora.embedder.get_model`，Fake model 的 `encode()` 必须记录文本、归一化、进度条和 NumPy 转换参数。
- RAG 集成测试 patch `aurora.rag.encode_query`，返回维度固定、可手算的 `float32` 向量。
- 每个涉及 `get_model` 的测试前后执行 `aurora.embedder.get_model.cache_clear()`，因为生产函数使用 `lru_cache(maxsize=1)`。
- 默认套件不得实例化 `SentenceTransformer`，不得读取 Hugging Face 缓存来“碰巧离线通过”。
### 7.3 VectorStore
- Aurora 当前没有外部向量数据库。普通测试使用真实的内存 `VectorStore` 和微型 `Chunk`/NumPy 矩阵，以验证真实排序、点积和序列化逻辑。
- 搜索向量使用 2 维手算数据；不得依赖 BGE 维度、真实 `data/index/` 或检索结果的本机状态。
- `save()`/`load()` 只对 `tmp_path` 下的显式目录操作，并验证 `vectors.npy` 与 `chunks.json`往返。
- AI Evaluation 需要真实 BGE 时使用版本化冻结索引，记录数据集版本与索引 hash；它不属于默认 pytest。
## 8. 三层防线
三层防线必须同时存在。任意一层都不能被视为另外两层的替代品。
| 防线 | 实施位置 | 防护范围 | 不覆盖的情况 | 自检方法 |
|---|---|---|---|---|
| OpenAI 客户端拦截 | autouse patch `aurora.config.OpenAI` 为构造即抛错的哨兵 | 所有仍通过项目 `get_client()` 构造 OpenAI 客户端的调用；即使业务模块已绑定 `get_client`，函数体仍从 `config` 全局解析 `OpenAI` | 绕过项目客户端的 HTTP/socket 直连；用例已注入的 ScriptedClient | `tests/test_infra.py` 调用未注入 Fake 的 `get_client()`，断言得到明确离线保护异常且没有连接尝试 |
| Socket 网络拦截 | autouse patch `socket.socket.connect` 和 `socket.create_connection` | SDK、模型下载及其他 Python socket 出站连接的兜底 | 不经这两个入口的非 socket IPC；已经建立的连接 | 用临时 socket 对保留测试地址发起连接，断言立即抛离线保护异常；不得访问真实域名 |
| BGE 模型加载拦截 | autouse patch `aurora.embedder.get_model` 为调用即抛错的哨兵，并清空缓存 | 直接调用 `get_model()` 以及 `encode_passages()`/`encode_query()` 经由该名字触发的真实模型加载 | 使用方已显式 patch 的 Fake model；绕过项目函数直接实例化 `SentenceTransformer` 的新代码由 socket 层兜底 | 调用 `encode_query("probe")`，断言在模型构造前失败；自检前后 `cache_clear()` |
在线用例只允许按 marker 范围解除所需防线。`online` 只放行 OpenAI 和 socket，仍拦截 BGE；`ai_eval` 只放行 BGE，仍拦截 OpenAI 和 socket。需要同时使用真实 API 与真实 BGE（或需要下载模型）的 AI Evaluation 必须同时标记 `online` 和 `ai_eval`。放行逻辑必须以 pytest item marker 为依据，不能因环境中存在 API key 就全局关闭保护；显式选择 `online` 但缺少 key 时测试必须失败，不得静默跳过。
## 9. 临时文件、测试数据与数据库
### 9.1 文件隔离
- 使用 `tmp_path` 创建每个用例独占的文件与目录；不要使用固定相对临时目录。
- `chunk_file()`、`load_cards()`、`VectorStore.save/load()` 和 `build_index.py` 集成测试必须显式指向临时路径。
- 测试中不得修改 `.env`；配置测试直接 patch `aurora.config.DEEPSEEK_API_KEY` 等已在导入时冻结的模块常量。仅 `monkeypatch.setenv()` 不能可靠改变已导入配置。
- 默认测试启动时必须在 `config.py` 导入前固定 dummy key 并拦截 `load_dotenv()`；应断言拦截已发生且冻结配置等于 dummy key，避免屏蔽机制因导入顺序变化而静默失效。显式 `online` 测试只接受进程环境变量中的 key，不读取 `.env`。
- 禁止读取真实用户数据、生产数据、仓库外 `../courseware_md/` 和被忽略的本地索引。
- 测试微型数据不得包含真实 API key、真实用户对话或未经授权的课件全文。
### 9.2 数据组织
- 软件测试数据放在 `tests/fixtures/`，保持最小、确定、可读和可手算。
- `llm_responses.py` 集中定义正常与畸形协议形状；测试仍须在使用处说明预期行为。
- AI Eval 黄金集与 `tests/fixtures/` 物理分离，按 `TEST_PLAN.md` 记录能力、难度、来源、可接受答案、禁用事实、轮次关系和审核者。
- 同一知识点的同义改写不得同时进入评测开发集和报告集。
### 9.3 数据库
Aurora 当前没有关系型数据库、外部向量数据库或持久化 Session，因此数据库隔离在当前实现中为 `N/A`，不得为了测试引入数据库。未来若增加数据库，测试必须使用每用例独立事务或临时实例，禁止连接生产地址，并在计划更新后再定义迁移、清理和并发规则。
## 10. Marker 与默认过滤
| Marker | 用途 | 默认执行 | 额外前置条件 |
|---|---|---:|---|
| 无 marker | Unit、Integration、离线 E2E | 是 | 三层防线全部开启 |
| `slow` | 显著超出默认套件时长的确定性测试 | 否 | 显式选择；仍默认断网、禁 BGE |
| `online` | 真实 DeepSeek API 协议冒烟 | 否 | 显式选择 + `DEEPSEEK_API_KEY` |
| `ai_eval` | 概率性能力评估；单独使用时只放行 BGE | 否 | 显式选择 + 数据集与索引版本；需要网络/API 时同时标 `online` |
一个用例可同时标记 `online` 和 `slow`；需要真实 API 与真实 BGE 的 AI 质量指标用例必须同时标记 `online` 和 `ai_eval`，不能仅标 `online` 混入协议冒烟。默认过滤固定排除 `online`、`ai_eval` 和 `slow`，与 `TEST_PLAN.md` 阶段 0 决策一致。
## 11. 分层编写规范
### 11.1 Unit
- 只验证一个模块内的函数或类，所有外部依赖可替换。
- 优先覆盖确定性输入输出、边界值、结构化解析、异常类型和副作用。
- 对 Router、Rewrite、Judge、Agent 等模型模块，测试的是请求和解析协议，不评判模型能力。
- 参数化畸形响应时，每个参数必须有场景 ID，并分别断言当前契约或计划指定的目标行为。
### 11.2 Integration
- 至少保留两个真实模块及其协作边界，只替换 LLM、Embedding、网络和真实文件位置。
- 应验证参数透传、分支互斥、返回结构、Session 写回时机、异常传播和输入未突变。
- `VectorStore`、`Session`、`tools` 等确定性组件应尽量使用真实实现；不要把整条链都 mock 掉后称为集成测试。
- `build_index.py` 使用临时语料、假 encoder 和临时输出目录，不加载真实 BGE、不写真实索引。
### 11.3 E2E
- 离线 E2E 从 `pipeline.run()` 的问题输入走到统一返回 dict 和 Session 写回。
- Pipeline、Session、tools、VectorStore 保持真实；模型边界使用有顺序的 ScriptedClient，Embedding 使用固定向量。
- 覆盖五类 Router 分支、多轮改写、`standalone` 的查询侧限定、`trace`/`hits` 互斥和失败轮次不污染历史。
- 离线 E2E 不等于真实模型质量验证。真实 API E2E 必须标记 `online`，只验证协议/schema 冒烟，不替代 AI Evaluation。
## 12. `xfail`、`skip` 与异常断言
### 12.1 xfail
`TEST_PLAN.md` 已指定的缺陷定标使用：
```python
@pytest.mark.xfail(strict=True, reason="DEF-01 关键字参数被丢弃")
```
- 断言正确的目标行为，让当前已知缺陷稳定 `XFAIL`。
- `reason` 必须包含 `DEF-xx` 和简短原因，并与缺陷登记对应。
- `strict=True` 强制修复后的 `XPASS` 令套件失败，提醒删除 xfail 并关闭缺陷。
- 不得用 xfail 包裹未知失败、偶发失败或未分析的环境问题。
- 计划指定“现状锁定”的用例使用普通 assert，不得擅自改成 xfail。
### 12.2 skip
- `skip` 仅用于运行前就能确定的外部前置条件缺失，例如显式选择 `online` 后没有 API key，或目标平台确实不支持某能力。
- `skip` 必须给出具体原因；不得用来隐藏缺陷、flaky、慢测试或尚未实现的计划用例。
- 默认 marker 过滤不是 skip；报告状态时应区分 deselected、skipped、not collected 和 not executed。
### 12.3 异常断言
- 使用 `pytest.raises(ExpectedType, match=...)` 精确断言异常类型和稳定的上下文文本。
- 不使用 `pytest.raises(Exception)`，除非计划明确要求验证宽泛边界本身；即使如此也应额外核对回退结果和副作用。
- 异常路径必须验证 Session、文件和调用记录没有半完成写入。
- 对计划明确要求转换的预期异常，应精确断言转换结果和副作用；当前工具计算链中的非实数 `ValueError`、表达式 `SyntaxError` 与参数个数/类型不符的 `TypeError` 均由 `execute_tool()` 转为模型可读错误。`AttributeError` 等白名单外的未预期异常仍应严格遵循计划的缺陷定标或现状锁定决策，不擅自写成成功契约。
## 13. 执行、失败、缺陷与状态更新
### 13.1 执行记录
每次阶段验收至少记录：
- 日期、Git revision、Python/pytest 版本和执行命令；
- collected、passed、failed、xfailed、xpassed、skipped、deselected 数量；
- 是否启用网络、真实 LLM、真实 BGE、索引版本和数据集版本；
- 失败 Test ID、错误摘要和产物位置；
- 未执行范围及原因。
只有本次确实被收集、执行并满足断言的用例才可标记 `Passed`。编写完成、静态阅读、上次通过、demo 成功或 pytest 未收集到用例都不能算通过。
### 13.2 失败处理
1. 先确认失败可复现，并核对三层防线是否仍生效。
2. 判断是产品缺陷、测试缺陷、基础设施缺陷、环境缺失还是概率性评测波动。
3. 保留原始断言和失败证据；不得先放宽断言、增加 skip 或删除用例。
4. 产品缺陷登记后按计划决定 `xfail(strict=True)` 或保持失败；没有计划授权不得自行定标。
5. 测试缺陷可以修正，但必须说明为什么原断言不符合真实契约，并复核相邻用例。
6. AI Eval 波动进入评测报告和人工复核，不应直接改动普通软件测试门槛。
### 13.3 缺陷登记
下一阶段应建立 `docs/testing/DEFECTS.md`，至少包含：缺陷 ID、关联 Test ID、优先级、当前行为、目标行为、复现命令、首次发现 revision、状态、负责人/决策人和关闭证据。`xfail`的 `reason` 必须引用同一缺陷 ID。
### 13.4 `TEST_STATUS.md`
下一阶段应建立 `docs/testing/TEST_STATUS.md` 作为执行状态，不复制 `TEST_PLAN.md`的设计内容。每次正式执行后追加或更新：
1. 基线与环境；
2. 实际命令和开关；
3. 分层/阶段结果统计；
4. Test ID 级失败、XFAIL 和未执行项；
5. 新增或关闭缺陷；
6. 与上次基线的变化；
7. 下一步和阻塞项。
状态使用 `Planned`、`Not Executed`、`Passed`、`Failed`、`XFailed`、`Blocked` 等明确术语。不得把 marker 排除的在线测试、AI Eval 或尚未实现的用例写成 Passed。状态文档必须保留执行命令和证据，不能只写汇总百分比。
## 14. 软件测试与 AI Evaluation 的边界
| 维度 | 普通软件测试 | AI Evaluation |
|---|---|---|
| 目标 | 验证确定性行为、协议、编排和失败路径 | 衡量分类、检索、生成、工具选择和多轮理解的质量分布 |
| 外部模型 | Fake/Stub；默认禁止真实模型 | 真实 LLM/BGE，不 Mock |
| 数据 | 微型、确定、可手算 fixture | 版本化黄金集与冻结索引 |
| 断言 | 精确契约，失败通常阻塞合并 | 聚合指标、分布、人工复核和趋势 |
| 执行 | 每次本地/PR 默认离线执行 | 显式、付费、手动或定时执行 |
| 结果 | Passed/Failed/XFailed | 报告、基线、回归趋势，不冒充软件测试通过率 |
Router 返回结构、坏 JSON 处理和请求参数属于普通测试；Router 分类准确率、macro-F1 和混淆矩阵属于 AI Evaluation。RAG 上下文格式和 query 透传属于普通测试；Recall/MRR、忠实度和引用质量属于 AI Evaluation。Agent 工具协议属于普通测试；模型是否选对工具、是否完成多步推理属于 AI Evaluation。
`scripts/eval.py` 与 `scripts/eval_report.py` 当前会访问真实 API、加载 BGE 并读取本地索引，只能作为显式 AI Evaluation 作业。当前 12 题是 smoke seed，不是泛化质量门槛；在完成 `EVL-META-001` 和首轮可信基线前，不设置计划外百分比阈值。
## 15. 本地与未来 CI 命令
### 15.1 本地交付检查
```powershell
uv sync --frozen --dev
uv run pytest --collect-only -q
uv run pytest -q
uv lock --check
git diff --check
git status --short
git diff --name-only
git diff --stat
```
报告必须逐项说明哪些命令已执行、哪些未执行及原因。不得把 import 成功、单个 demo 成功或单个测试文件成功概括成“全部测试通过”。
### 15.2 未来 PR CI
`TEST_PLAN.md` 已确定每个 PR 的离线命令：
```yaml
- uv sync --frozen --dev
- uv run pytest -q
- git diff --check
- uv lock --check
```
CI 使用 Python 3.14，不注入 `DEEPSEEK_API_KEY`，上传 JUnit XML 以保留 Test ID 和失败证据。阶段 1 至 4 稳定运行两周无 flaky 后再建立该阻塞门禁。
在线冒烟和 AI Evaluation 使用独立的手动/定时 job：在线冒烟只验证五类协议和多轮 schema；AI Evaluation 记录模型名、日期、温度、采样数、数据集版本、索引 hash、延迟、token 和估算费用。它们不进入普通 PR 的默认通过条件。
## 16. 当前差异与待确认技术决策
### 16.1 计划与当前仓库状态差异
1. `TEST_PLAN.md` 的审计基线记录当时 `pyproject.toml` 无 pytest、`.venv` 未安装 pytest；当前 pytest 9.2.0 已安装，并已纳入 `pyproject.toml` 与 `uv.lock`。
2. `TEST_PLAN.md` 与 `docs/codebase-overview.md` 已区分审计时点和当前状态，不应再据历史记录判断当前没有 pytest。
3. 当前工作区已建立 `tests/`、pytest 配置、三层离线防线、`DEFECTS.md` 和 `TEST_STATUS.md`；阶段 0 状态以 `TEST_STATUS.md` 最近一次实际执行记录为准。CI 仍未建立。
未发现需要改变 `TEST_PLAN.md` 已确定测试范围、优先级、marker、三层防线或阶段顺序的代码冲突。计划中列出的产品行为冲突和缺陷继续按其用例与待决项处理。
### 16.2 尚未确定的技术决策
以下决策沿用 `TEST_PLAN.md`，本规范不代替产品决策：
1. `store=None` 进入 RAG 分支的失败/降级契约。
2. RAG `content=None` 的异常或降级方式，以及是否写回 Session。
3. Agent 达 `max_rounds` 的异常或结构化状态契约。
4. validator 如何区分中间计算与最终输出。
5. `execute_tool()` 精度和布尔字面量规则；非实数、表达式语法错误与参数个数/类型错误（`TypeError`）的转换边界已按 TOL-UNIT-006、AGT-INT-003、DEF-01 落实；白名单外的 `AttributeError` 等仍未定契约。
6. 全角字符清洗与索引重建/版本化策略。
7. `Session.context()` 返回内部列表还是副本。
8. DeepSeek 超时与重试是否成为显式项目配置。
9. AI 裁判独立性、课件衍生评测集授权和质量回归阈值。
这些决策应先登记到缺陷或决策记录，再更新对应目标断言。不得由测试作者通过选择“容易通过”的断言替代产品决策。
## 17. 下一阶段基础设施清单
阶段 0 已按 `TEST_PLAN.md` 建立以下基础设施；后续整改与验收仍不得提前编写计划外业务测试：
1. 已完成：核对 pytest dev 依赖并更新 `uv.lock`。
2. 增加 `[tool.pytest.ini_options]`、三个 marker 和默认过滤。
3. 创建 `tests/conftest.py`，实现三层防线、`ScriptedClient` 工厂和响应构造器。
4. 创建 `tests/test_infra.py`，验证 OpenAI、socket、BGE 拦截与 Fake 注入。
5. 创建计划规定的目录和最小 fixture 数据，不复制真实课件或索引。
6. 建立 `docs/testing/DEFECTS.md` 与 `docs/testing/TEST_STATUS.md`。
7. 执行 `uv run pytest --collect-only -q` 和 `uv run pytest -q`；只有实际得到计划要求的成功结果后更新状态。
8. 完成阶段 0 准入后，再按 P0 和阶段 1 至 4 的顺序实现测试；在线与 AI Evaluation 保持独立显式作业。
