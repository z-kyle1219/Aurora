# Aurora Repository Guidance

本文件是 Aurora 仓库的项目级 AI Coding 约束。它用于指导开发行为，不替代 `README.md` 或 `docs/codebase-overview.md`。

## 1. 项目认知

Aurora 是面向《模拟电子技术》课程的 Python 智能助教原型。当前完整编排入口是 `src/aurora/pipeline.py:run()`；`scripts/chat_loop.py` 仍调用早期的 `companion_v0`，不代表完整 Pipeline。

核心技术栈：

- Python `>=3.14`，使用 `uv`、`pyproject.toml` 和 `uv.lock` 管理环境与构建。
- DeepSeek OpenAI 兼容接口负责问题改写、Router、答案生成和 LLM-as-judge。
- `sentence-transformers` 的 `BAAI/bge-small-zh-v1.5` 负责本地 Embedding。
- NumPy 和项目内 `VectorStore` 负责向量存储与 top-k 检索。
- 项目使用自研同步 Python 编排，没有 LangChain、LangGraph、Web API、数据库或持久化 Session。

核心边界：

- `pipeline.py` 负责统一编排与收口，同时承载分支提示词、`RAG_PROMPTS` 分派表、`OPAMP_VALIDATORS` 注册表、固定物理常量和 `fallback_answer()` 文案。这些都是当前模块边界的一部分，不要仅因其不是纯编排逻辑就擅自迁移。
- `rewrite.py` 负责多轮追问改写。
- `router.py` 负责五类标签分流。
- `rag.py` 负责检索增强生成。
- `agent.py` 与 `tools.py` 负责 Tool Use、执行回环和物理校验。
- `session.py` 负责进程内对话历史。
- `chunker.py`、`card_loader.py`、`embedder.py`、`vector_store.py` 负责知识入库与检索数据层。
- `judge.py`、`judge_meta.py` 与 `scripts/eval*.py` 负责评测，不属于在线回答主链；`judge_meta.py` 只做裁判与人工标签的一致性统计，不设质量门槛。

## 2. 文档阅读规则

`docs/codebase-overview.md` 是当前项目的代码级现状说明，记录架构、调用链、数据结构、状态管理、测试现状和已知风险。该文件存在时，按任务读取对应标题，不要求为无关的小改动通读全文：

- 修改 Pipeline、Router 或入口：阅读“模块地图”“完整问题处理链路”“已知技术债务与风险”。
- 修改 RAG、切块、Embedding 或向量库：阅读“RAG 实现”“数据结构与接口约定”“已知技术债务与风险”。
- 修改 Tool Use、计算器或校验回环：阅读“Tool Use 与校验回环”“数据结构与接口约定”“已知技术债务与风险”。
- 修改 Session、多轮消息或问题改写：阅读“完整问题处理链路”“Session 与多轮状态”“数据结构与接口约定”“异常处理与日志”“已知技术债务与风险”。
- 修改配置、异常处理或模型调用：阅读“配置管理”“异常处理与日志”“已知技术债务与风险”。
- 修改测试或评测：阅读“测试与评测现状”。

`README.md` 面向使用者，可能包含历史设计或规划；不得仅根据 README 推断当前实现。代码、测试和真实调用关系与文档不一致时，以当前代码为准，并在任务总结中指出不一致。若本次修改改变了文档描述的现状，应同步更新对应文档；不要静默留下过期说明。

## 3. 架构与变更约束

- 遵循最小改动原则。先确认目标行为和现有边界，不顺带重构无关模块。
- 不得仅凭文件名或注释判断依赖关系；通过 import、调用点、参数传递和返回值确认实际调用链。
- 不得未经分析改变模块职责，或把编排、模型调用、状态和数据访问逻辑任意混合。
- 修改跨模块函数签名、普通字典字段、dataclass、消息格式或返回值前，优先使用 `rg` 检查全部调用方和被调用方；`rg` 不可用时使用环境已有的等价工具，如 `grep -rn`，或 PowerShell 的 `Get-ChildItem -Recurse -File | Select-String`。
- 修改公共数据结构时，检查 demo、评测脚本、Session 写回、trace/hits 展示和磁盘数据兼容性。
- 不为单个功能无理由引入框架或依赖。新增依赖前必须说明现有标准库和项目依赖为何不足，并评估锁文件、模型下载、运行环境与下游调用影响。
- 保持当前同步编排模型，除非需求明确要求并发或异步；引入并发前必须分析共享状态、模型客户端、Session 和文件写入的竞态。
- 保留以下现有语义，除非任务明确要求改变：
  - `standalone` 用于 Router 和 RAG 查询，不进入生成侧，也不写入 Session。
  - Session 长期历史只保存用户原话和最终回答，不保存本轮 tool call 过程。
  - 五条分支在 `pipeline.run()` 统一写回 Session 并返回统一结果。
  - RAG 分支需要可用的 `VectorStore`；`fallback` 不检索、不调用答案生成模型。

新增或重命名电路类型时，至少同步核对以下耦合点，不能只改 Router 提示词：

- `router.py`：`TYPE_*` 常量、`CIRCUIT_TYPES`、`TYPE_LABELS`；`ROUTE_TOOL` 的 enum 引用 `CIRCUIT_TYPES`，需确认最终 schema 包含新类型。
- `pipeline.py`：类型导入、分支条件、专用提示词和 `RAG_PROMPTS`；非 RAG 类型还要核对 validator 或 fallback 行为。
- `data/eval_set.json`：新增该类型的代表性标注案例，并检查现有 `circuit_type` 标注。
- `scripts/demo_router.py`、`scripts/eval.py`、`scripts/eval_report.py` 等所有读取类型标签或展示 `TYPE_LABELS` 的调用方。

## 4. 编码规范

- 沿用现有 Python 风格、中文模块文档和命名方式；不要为局部修改批量改格式或改写注释。
- 函数保持单一职责。只有在消除真实重复、隔离明确边界或匹配既有模式时才新增抽象。
- 新增或修改公共函数时使用类型注解写清参数与返回类型；docstring 用于补充行为、约束和异常语义。对外部输入、模型结构化参数和文件内容做与风险匹配的校验。
- 保持现有接口约定明确：消息为 OpenAI 兼容 dict，Pipeline 返回 dict，检索对象使用 `Chunk` 和 `SearchResult`。改变这些约定必须同步检查所有消费者。
- 外部 API、模型调用、文件读写、JSON 解析、索引加载和数值计算必须分析失败方式。错误应在能提供有效上下文或实施明确降级的边界处理。
- 不得使用宽泛异常捕获来隐藏编程错误。若业务边界确实要求 `except Exception` 降级，必须明确回退行为、状态是否写入及可观测影响，并为该边界补充验证。
- 不得静默吞掉异常后伪造成功结果。返回错误字符串、回退原问题或默认分类时，要保持调用方能够区分实际行为。
- 处理半可信模型输出时，不使用 `eval()` 或未经约束的动态执行。
- 修改计算工具链时必须先处理或保留已知边界：`execute_tool()` 已将 `SyntaxError`、复数结果、超长表达式、超限幂运算和 `TypeError`（参数不匹配）统一转换为 `"计算失败：…"` 字符串，预期错误不会穿出工具边界；`_eval_node()` 对 `node.keywords` 非空时显式抛 `ValueError` 拒绝，不静默丢弃。仍未覆盖的是 `run_agent()` 层模型调用的统一异常兜底：网络、鉴权、限流等传输层异常原样向上抛出；截断（`finish_reason="length"` 且无内容）与空内容抛 `RuntimeError`，属无降级的硬失败。失败路径契约未定的部分见 `docs/testing/TEST_PLAN.md` §13。不得在不了解这些传播路径时扩大工具输入范围。

## 5. 状态管理规范

- 修改 `Session`、history 或消息构造前，说明状态由谁创建、读取、写入，以及状态在何时失去引用，是否跨轮次、请求、用户或进程存在。当前没有显式销毁机制。
- 保持不同 Session 实例的数据隔离；不得使用可变全局对象保存用户对话状态。
- 分析每个异常路径的写回时机。当前 Pipeline 只在分支成功得到答案后调用 `Session.add_turn()`；不得让失败轮次只写入半条消息或错误地污染历史。
- Session 约定保存交替的 `user`/`assistant` 纯文本消息，但当前类本身不做 role/schema 校验。依赖该约定的修改必须显式检查输入来源。
- `Session.context()` 返回内部 `history` 列表本体。当前 `run_agent()` 和 `ask_rag()` 通过 `messages.extend(history)` 构造新列表，不会因追加 `messages` 直接改变 history；但任何新代码都不得原地追加、删除或改写 `history` 及其中的消息 dict，除非明确通过 Session 写接口更新状态。
- `pipeline.run()` 未传 `session=` 时会静默创建临时 Session。多轮实现和验证必须显式复用同一个 Session，否则每轮都是单轮状态。
- 修改历史窗口、摘要或持久化机制时，评估问题改写、Router、RAG、Tool Agent 和旧调用方的行为变化。
- 涉及线程、异步任务或多进程时，必须分析共享列表、文件索引、客户端生命周期和写入顺序的竞态；不能把单进程演示行为直接视为并发安全。

## 6. 测试与验证规范

`docs/testing/TEST_PLAN.md` 是测试范围、稳定 Test ID、优先级、阶段安排和阶段准入标准的唯一依据；`docs/testing/TESTING_GUIDE.md` 是测试实施、Mock/Fake/Stub、Fixture、外部依赖隔离和 marker 的依据。修改测试或评测前必须阅读两者的相关章节，不得把实施规范全文复制到本文件。测试相关文档之间如何分工、什么信息该写进哪一份，见 §8「文档维护规则」。

测试基础设施尚未建立或尚未通过对应阶段验收时，不得因 pytest 已安装而假设三层防线、Mock、pytest 配置或离线保护已经生效。实施测试基础设施时，必须先验证保护机制，再开始业务测试；未达到阶段准入标准的能力仍视为未实现。

- AI 修改业务代码时，必须检查受影响调用链已有测试和 `TEST_PLAN.md` 对应用例，维护相关测试并执行与风险匹配的回归验证；若计划缺少范围或稳定 Test ID，应先更新并评审计划，不得在测试代码中自行扩张范围。
- 测试函数必须使用 `TEST_PLAN.md` 登记的稳定 Test ID，并保持测试报告可从 pytest node id 追溯到该 ID。AI 不得通过测试断言自行决定计划中尚未确定的产品行为契约；此类事项应保持 `Planned`/`Blocked` 或按计划登记缺陷并等待决策。
- 普通 `uv run pytest -q` 必须保持离线：不得调用真实 LLM API，不得下载或加载真实 BGE，不得读取真实索引或仓库外课件。真实 API、模型和 AI Evaluation 只能按计划使用显式 marker、开关和独立命令运行。
- Mock、Fixture、三层防线、临时文件和补丁落点必须遵守 `TESTING_GUIDE.md`。模型替身必须复现调用方实际访问的响应形状，并验证适用的调用次数、关键参数和消息顺序，不能只返回固定值。
- 不得通过删除测试、降低断言标准、放宽异常类型、随意增加 `skip`/`xfail` 或缩小收集范围制造通过结果。失败应先分类并保留证据；`skip`/`xfail` 仅能用于计划和实施规范明确允许的情形。
- 只有本次实际被收集、执行且满足断言的用例才能标记 `Passed`。未执行、未收集、被 marker 排除、被 skip 或仅静态审阅的用例不得标记为通过。
- 每次验收按 `TESTING_GUIDE.md` §13.1 的保留标准写入 `docs/testing/runs/` 下的新文件，写入后不再修改；`TEST_STATUS.md` 只写当前状态，其结构、粒度与禁止项见同一文件 §13.4。缺陷状态与关闭证据按 `docs/testing/DEFECTS.md` 维护。

- 新增功能必须提供与风险相称的验证；新增可复用纯逻辑时优先补充自动化测试，而不是只扩展 demo。
- Bug 修复必须验证原始失败条件，并考虑能稳定复现该问题的回归测试。
- 修改公共接口时，检查全部调用点，并至少验证主链和相关分支的输入、返回值及异常路径。
- 纯函数优先做不访问网络、不写真实数据的局部验证，例如切块、卡片解析、计算器、向量排序或结果格式化。
- `get_client()` 在各模型模块中直接导入并调用，当前没有显式客户端注入接口。离线测试 Router、RAG、Agent 或 Pipeline 时，需要在实际使用模块中 patch `get_client`（例如 patch `aurora.router.get_client`，而非只 patch `aurora.config.get_client`），并提供符合 OpenAI 响应形状的替身；否则集成验证会访问真实 API。
- `scripts/demo_*.py` 是演示，`scripts/eval.py` 和 `scripts/eval_report.py` 是依赖真实模型的在线评测；它们不等同于隔离的单元测试。
- `uv run scripts/build_index.py` 会读取仓库外课件、加载或下载 Embedding 模型并写入 `data/index/`。运行前评估环境、网络和文件副作用。
- `uv run scripts/eval.py`、`uv run scripts/eval_report.py` 和多数 Pipeline demo 会调用 DeepSeek API，产生网络请求、延迟和费用。仅在任务需要且配置可用时运行，并如实报告范围。
- 完成前至少执行适用的静态检查，如 `git diff --check`。同时检查 `git status --short` 以覆盖未跟踪文件，并结合 `git diff --name-only`/`git diff --stat` 核对已跟踪文件的改动范围；不能只看 `git diff` 后断言工作区仅包含预期文件。
- 不得把代码生成成功、导入成功或单个 demo 成功视为全部任务完成。
- 不得虚构测试结果，也不得声称执行过未执行的命令。明确列出已执行、未执行及受环境限制的验证。

## 7. AI Coding 工作流程

按以下顺序完成代码任务：

1. 明确需求、非目标和可验证的验收标准。
2. 按第 2 节读取相关文档和最小必要源码，确认工作区已有改动并保留用户修改。
3. 追踪实际调用链、数据流、状态写回、异常路径和受影响调用方。
4. 在修改前给出简洁实施方案；涉及接口、依赖、持久化、并发或外部副作用时，明确风险与验证方式。
5. 按最小改动原则实现，不处理任务范围外的问题。
6. 执行与风险匹配的测试、静态检查和回归验证；外部调用或写操作按第 6 节评估。
7. 总结修改文件、行为变化、已执行验证、未执行验证和剩余风险。

仅修改文案、注释、拼写或孤立文档链接，且不改变运行行为、接口或命令时，可采用轻量流程：确认目标位置和引用关系后直接修改，执行 `git diff --check`、`git status --short` 和范围核对，再总结结果；无需展开完整调用链或单独等待实施方案确认。

诊断、解释或代码审查任务默认只读；用户未要求修复时，不修改代码。用户明确要求只分析或只写文档时，严格遵守对应范围。

## 8. 文档维护规则

写任何文档前先问一句：**这条信息会变吗？**

| 信息性质 | 例子 | 写在哪 |
|---|---|---|
| 不会变 | 期望行为、边界、为什么这样设计 | `TEST_PLAN`、`TESTING_GUIDE`、`README` 的对应章节 |
| 会变，且是「现在怎样」 | 哪个阶段过了、当前卡在哪 | `docs/testing/TEST_STATUS.md`，覆盖更新，不累加 |
| 会变，且是「某次验收」 | 某天在哪个 revision 验收、判定是什么、有哪些不可复现的例外 | `docs/testing/runs/YYYY-MM-DD-stage<N>.md` 或 `YYYY-MM-DD-def<NN>.md`，一次验收一个文件，写入后不修改 |
| 会变，且是「某个实体的当前状态」 | 缺陷是开着还是已关闭 | `docs/testing/DEFECTS.md`，就地更新该缺陷行的状态与关闭证据 |
| 已经发生 | 谁改的、何时、为什么 | 不写，`git log` 就是记录 |

同一件事不得写进两个文件。需要引用另一份文档时写路径指过去，不复制内容：执行读数只写进对应的 `runs/` 文件；`TEST_STATUS.md` 只保留当前判定和证据指针；其他文档提到测试结果时只引用 `TEST_STATUS.md` 或对应 run，不复制通过数量。测试用例是否已实现，以 `tests/` 下的函数名为准，不在文档里手工维护清单。

- 架构、模块职责、主调用链、RAG、Tool Use 或 Pipeline 分支发生变化时，更新 `docs/codebase-overview.md` 的对应章节。
- 公共接口、消息格式、返回结构、Session 生命周期或异常写回机制变化时，同步更新接口与状态说明。
- 用户可见的安装、运行命令或能力边界变化时更新 `README.md`。
- 重要的产品/架构决策写在 `README.md` 的「设计要点」，或写进对应 commit 的 body；不单独建 `docs/decisions/`，避免与 README 重复。
- 小型 Bug 修复只更新被其直接影响的说明，不为制造“文档完整度”批量改写无关内容。
- 文档陈述必须区分已实现、未实现和未知；计划不得写成当前能力。
