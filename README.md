# Aurora ⚡ 电路智能助教

一个面向《模拟电子技术》课程的 **Agent 作品项目**：先判断学生问的是哪类电路，再分派到对应的解题链路——能算的交给工具算，该查的走检索，不会的诚实降级。

> 项目名取自「极光」：太阳的带电粒子流穿越太空，在磁场中化作流光。既是电，也是自然，也寓意「在迷惘中点亮知识」。

**重点不在"能聊天"，而在"解题流水线可被评测"**：每一次分类、每一次工具调用、每一次校验都有记录，能算出准确率。

---

## 能力边界

Aurora 目前只做模拟电子技术的五个分支，边界写死在这里，超出就降级：

| 类型标签 | 覆盖内容 | 处理方式 |
|---|---|---|
| `opamp` | 集成运放运算电路：比例、求和、积分、微分，求具体输出电压 | function calling 调用计算工具 + 电压合理性校验回环 |
| `feedback` | 负反馈组态判断、深度负反馈 `A_f ≈ 1/F`、反馈对性能的影响 | RAG 检索 + 决策树提示词 |
| `power` | 直流稳压电源：变压器→整流→滤波→稳压 | RAG 检索 + 链式流程提示词 |
| `concept` | 虚短虚断、电流源、放大器一般概念 | RAG 检索 + 学伴提示词 |
| `fallback` | 数字电路、时序逻辑、编程等超纲问题 | 不检索、不硬编，直接说明能力范围 + 给学习路径 |

**明确不做**：电路图识别（只处理文字描述的电路）、手写公式 OCR。

---

## 架构

```
                        学生问题
                            │
                    ┌───────▼────────┐
                    │  Router        │  LLM 分类，function calling 强制返回
                    │  route_circuit │  {类型, 置信度, 理由}
                    └───────┬────────┘
                            │ 类型标签
        ┌───────────┬───────┴───────┬───────────┐
        ▼           ▼               ▼           ▼
     opamp      feedback         power       concept      fallback
        │           │               │           │            │
        ▼           ▼               ▼           ▼            ▼
   run_agent    ask_rag         ask_rag     ask_rag      模板回答
   tool use    ＋决策树         ＋链式      ＋学伴        ＋学习路径
   ＋校验回环    提示词          流程提示词   提示词
        │           │               │           │            │
        └───────────┴───────┬───────┴───────────┘            │
                            ▼                                │
                    结构化报告：类型 → 判据 → 计算 → 结论      │
```

分层的意义：**能力层只做一件事、能单独测；编排层只做决策。**

| 层 | 模块 | 职责 |
|---|---|---|
| 编排层 | `pipeline.py` | 拿到类型标签后走哪条路、挂哪个校验器 |
| 能力层 | `router.py` | 给问题贴类型标签（LLM 分类） |
| 能力层 | `rag.py` | 检索 → 拼上下文 → 生成 |
| 能力层 | `tools.py` | 计算工具（ast 白名单）+ 物理合理性校验 |
| 能力层 | `agent.py` | function calling 调用回环 |
| 数据层 | `chunker.py` / `card_loader.py` / `embedder.py` / `vector_store.py` | 切块 → 向量化 → 余弦检索 |

---

## 技术栈与关键选型

| 决策 | 选择 | 理由 |
|---|---|---|
| 生成模型 | DeepSeek `deepseek-chat` | OpenAI 兼容接口，换 `base_url` 即可，不绑定 SDK |
| Embedding | `BAAI/bge-small-zh-v1.5`（512 维，本地） | 中文效果好、离线免费、无 API 依赖 |
| 向量检索 | **手写 numpy 余弦**，不用 Chroma / FAISS | 归一化后余弦退化为点积，`30 行代码`能讲清原理；数据量小，性能不是瓶颈 |
| Agent 框架 | **手写 agent loop**，不用 LangChain / LangGraph | 几十行就能跑通回环，先理解本质再谈对比 |
| 安全求值 | `ast` 白名单，拒绝 `eval()` | 模型输出是半可信输入，必须做语法白名单 |
| 环境 | `uv` + Python 3.14 | 依赖锁定（`uv.lock`），环境可复现 |

---

## 目录结构

```
aurora/
├── src/aurora/
│   ├── config.py          # 读 .env → 返回 DeepSeek 客户端
│   ├── companion_v0.py    # W1 学伴 v0：纯提示词对话（含流式版）
│   ├── chunker.py         # 课件 markdown 切块（清乱码 / 认标题 / 600 字封口）
│   ├── card_loader.py     # 公式卡片解析（字段无关，每张卡 = 一个 chunk）
│   ├── embedder.py        # bge 向量化（文档不加前缀 / 查询加 BGE 前缀）
│   ├── vector_store.py    # 手写向量库：add / search / save / load
│   ├── rag.py             # RAG 编排：检索 → 拼上下文 → 生成
│   ├── tools.py           # 计算工具 + TOOLS schema + 执行分发 + 运放校验
│   ├── agent.py           # function calling 调用回环 + 校验钩子
│   ├── router.py          # 五类分流（LLM 分类，tool_choice 强制结构化）
│   └── pipeline.py        # 编排层：Router 分流 → 五条链路分派
├── scripts/
│   ├── demo_companion.py  # W1 单轮示例
│   ├── chat_loop.py       # W1 多轮对话（手动维护 history）
│   ├── build_index.py     # 离线建库：切块 + 卡片 → 向量化 → 落盘
│   ├── demo_rag.py        # RAG 问答演示
│   ├── demo_tools.py      # tool use 演示（正常计算 / 超压校验回环）
│   ├── demo_router.py     # Router 演示（五类各一题看分流）
│   └── eval.py            # 评测：分类准确率 + 答案正确率
├── knowledge/
│   ├── formula_cards.md   # 公式卡片（8 张，教材复现）
│   └── pages/             # 教材切页 PNG（体积大，已 gitignore）
├── data/
│   ├── eval_set.json      # 评测集（12 题，覆盖 5 类）
│   └── index/             # 向量库产物（已 gitignore，可重建）
└── .env.example / pyproject.toml / uv.lock
```

---

## 快速开始

**前置**：Python 3.14+、[uv](https://docs.astral.sh/uv/)、一个 DeepSeek API Key。
课件源文件放在项目**上一级**的 `courseware_md/`（10 个 markdown）。

```bash
cd aurora

# 1. 配置密钥
cp .env.example .env        # Windows: copy .env.example .env
#    编辑 .env，填入 DEEPSEEK_API_KEY

# 2. 安装依赖
uv sync

# 3. 建向量库（首次会下载 bge 模型约 100MB，之后走缓存）
uv run scripts/build_index.py

# 4. 跑演示
uv run scripts/demo_rag.py      # RAG 问答
uv run scripts/demo_tools.py    # 工具调用 + 校验回环
uv run scripts/demo_router.py   # 五类分流（推荐先跑这个，一眼看懂全局）

# 5. 跑评测
uv run scripts/eval.py
```

> 无 VPN 时下载模型可先设 `HF_ENDPOINT=https://hf-mirror.com`。

### 示例输出（开发时的运行记录）

`opamp` 链路的校验回环，输入 `ui = -3V`、增益 `-10`，算出 `30V` 超过 `±15V` 电源：

```
- 调用 calculate_expression({"expr": "-(100/10)*(-3)"}) → 30
  [!] 校验警告: 算出的 30V 已超出运放电源电压 ±15V，说明运放饱和，
      实际输出会被钳位在 +15V。请基于钳位后的值修正结论。
→ 最终结论：uo = +15V（运放已进入饱和区）
```

---

## 评测

`scripts/eval.py` 跑完整流水线（Router → 分派 → 生成），逐题对比标注。

| 指标 | 首版结果 | 判分方式 |
|---|---|---|
| 分类准确率 | 12 / 12 | 类型标签字符串精确对比 |
| 答案正确率 | 12 / 12 | 期望关键词命中（启发式） |

**这两个 100% 需要打折看**：测试集是照着当前能力手写的（12 题、5 类各 2~4 题），答案判分是关键词命中，`"整流"`、`"平滑"` 这类词很容易被顺带命中。它证明的是"链路通了、没跑偏"，**不能**当成泛化能力。升级计划见 Roadmap。

---

## 设计要点（面试常问的三个问题）

**1. 为什么手写向量检索，不用 Chroma / FAISS？**
向量入库时已 `normalize_embeddings=True`，模长都是 1，余弦相似度退化为点积；一次 `vectors @ query` 就是矩阵乘法（底层 BLAS），30 行代码拿走"检索"这个黑盒。数据量在千级以下时，引入向量数据库只增加依赖，不增加能力。

**2. 为什么计算必须走工具，而不是让模型算？**
LLM 做多位数乘除会出错，而且错误是"看起来合理"的——这是最难排查的一类幻觉。所以提示词里写成铁律：凡涉及算术必须调用工具，工具结果即标准答案，模型不得重算。

**3. 工具算完为什么还要校验？**
工具只保证"算术没错"，不保证"物理上说得通"。运放输出 30V 而电源只有 ±15V，算术正确、结论错误。所以 `agent.py` 预留了 `validators` 钩子：工具返回后先过一遍物理约束，不通过就把警告作为 `tool` 消息回传给模型，让它自己修正结论。**这条回环才是"校验"和"聊天"的区别。**

---

## 已知局限

诚实列出来，也是下一步的待办：

- **没有自动化测试**：`chunker` / `vector_store` / `tools` 都是纯函数，测试成本很低，目前为零。
- **答案判分太松**：关键词命中容易误判为正确，需要 LLM-as-judge 二分类判分。
- **只支持单轮**：`pipeline.run()` 不接收 `history`，学生追问"那 R1 换成 20k 呢"会断线（`ask_rag` 已支持 history，编排层没接）。
- **检索无相似度阈值**：永远返回 top-3，问完全无关的问题也会硬塞片段进来，是幻觉入口。
- **Router 的置信度没用上**：`route()` 返回了 `confidence`，编排层没据此降级或反问澄清。
- **校验器是演示级**：电源电压硬编码 `15V`，没有从题干抽取参数。
- **没有界面**：只能跑命令行脚本。
- **无日志 / 无成本统计**：`trace` 只在内存里，没落盘，也没统计每次问答的耗时与 token。

---

## Roadmap

- [x] **W1 学伴 v0**：纯提示词对话，跑通"messages 构造 → 调模型"
- [x] **W2 RAG**：课件切块 → bge 向量化 → numpy 余弦检索 → 引用回答
- [x] **W2 收尾 公式卡片**：教材为扫描版无法 OCR，改"切页保真图 + 读图转写"成 8 张结构化卡片，与课件块混入同一向量库
- [x] **W3 tool use**：`calculate_expression`（ast 白名单）+ function calling 回环 + 运放电压校验回环
- [x] **W3 Router**：LLM 分类五类分流，校验器按类型自动挂载；12 题评测集跑通
- [ ] **W4 工程收尾**：单元测试 + LLM-as-judge 评测 + 混淆矩阵 + 日志与耗时统计
- [ ] **W4 可演示**：Streamlit 界面 + 多轮会话
- [ ] **W4 对比实验**：`feature/langgraph` 分支用 LangGraph 重写同一流程，对比代码量、可调试性与延迟

---

## 数据来源

- 课件：大连理工大学《模拟电子技术》课程 PPT（10 份），经 markitdown 转为 markdown 作为检索语料。
- 教材：《电子技术基础·模拟部分》（第六版，康华光），扫描版仅用于公式核对与卡片转写，不随仓库分发。

仅供教学与个人学习使用。
