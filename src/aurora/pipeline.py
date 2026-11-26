"""W3 电路分析智能体 —— 编排层（Router 分流 + 各链路分派）。

这是把上一轮写好的三块能力（RAG、tool use、校验回环）真正「串成一个智能体」
的地方：入口只有一个 run()，它先问 Router「这是哪类电路」，再按标签分派。

分层回顾（面试常问的「能力层 vs 编排层」）：
    - 能力层：rag.py（检索）、tools.py（算数+校验）、router.py（分类）
      每一块都只做一件事、都能单独测试；
    - 编排层：本文件，负责「决策」——拿到类型标签后走哪条路、挂什么校验。

意图优先分派。概念解释和学习指导直接生成，资料定位走 RAG，
题目缺关键条件时先澄清，其余按电路类型执行：
    opamp    → run_agent（tool use + 电压校验回环，校验器在这里自动挂上）
    feedback → ask_rag + 决策树提示词
    power    → ask_rag + 链式流程提示词
    concept  → ask_rag + 默认学伴提示词
    fallback → 模板直接给「暂不支持 + 学习路径」（不检索、不装懂）
"""

from aurora.agent import run_agent
from aurora.config import DEEPSEEK_MODEL, get_client
from aurora.rag import ask_rag
from aurora.rewrite import rewrite_question
from aurora.router import (
    INTENT_ANALYZE,
    INTENT_EXPLAIN,
    INTENT_LOCATE,
    INTENT_OUT_OF_SCOPE,
    INTENT_STUDY,
    TYPE_FALLBACK,
    TYPE_FEEDBACK,
    TYPE_OPAMP,
    TYPE_POWER,
    route,
)
from aurora.session import Session
from aurora.tools import validate_opamp_output
from aurora.vector_store import VectorStore

# 运放电源电压：输出电压不能超过它（校验回环用到的物理约束）
SUPPLY_VOLTAGE = 15.0

# 校验器：只有「运放运算电路」才挂电压校验——这正是 Router 要解决的
# 「校验器按电路类型自动启用」问题（上一轮 demo 里是手动指定的）。
OPAMP_VALIDATORS = {
    "calculate_expression": lambda result: validate_opamp_output(result, SUPPLY_VOLTAGE),
}

# 负反馈判断：决策树提示词（复用 RAG 检索，只换「怎么引导模型」）
FEEDBACK_PROMPT = """你是「Aurora」，一位严谨的模拟电子技术课程助教，当前任务是判断负反馈组态。

现在我会给你几段从课件/教材检索出来的相关内容，用【课件片段】标记并注明来源。

请按「决策树」逐步判断，不要跳步，每一步都说明判据：
1. 输出采样：反馈取自输出电压还是输出电流？
   - 取自电压（反馈网络与负载并联取样）→ 电压负反馈，能稳定输出电压
   - 取自电流（反馈网络与负载串联取样）→ 电流负反馈，能稳定输出电流
2. 输入连接：反馈信号与输入信号在输入端是串联还是并联？
   - 串联（输入回路异名端相连）→ 串联负反馈，增大输入电阻
   - 并联（输入回路同名端相连）→ 并联负反馈，减小输入电阻
3. 组合两者得到组态：电压/电流 × 串联/并联 → 四种组态之一。

回答规则：
1. 只能基于提供的片段作答，在相关句子末尾注明来源（如「来源：公式卡片：…」）；
2. 最终结论用一句话给出确定的组态名称（如「电压串联负反馈」）；
3. 片段不足时诚实说明，不要编造。"""

# 直流稳压电源：链式流程提示词
POWER_PROMPT = """你是「Aurora」，一位严谨的模拟电子技术课程助教，当前任务是分析直流稳压电源。

现在我会给你几段从课件检索出来的相关内容，用【课件片段】标记并注明来源。

请按「链式流程」逐步展开，不要跳步：
变压器（降压）→ 整流电路（交流变直流）→ 滤波电路（平滑脉动）→ 稳压电路（稳定输出）
每一步说明它的作用与关键器件。

回答规则：
1. 只能基于提供的片段作答，注明来源；
2. 片段不足时诚实说明，不要编造。"""

# 类型标签 → 专用提示词（feedback / power 各一套，concept 用 rag.py 的默认提示词）
# 用查表代替 if/elif：三条链路只差一个提示词，不值得写两段几乎相同的分支
RAG_PROMPTS = {
    TYPE_FEEDBACK: FEEDBACK_PROMPT,
    TYPE_POWER: POWER_PROMPT,
}

DIRECT_PROMPTS = {
    INTENT_EXPLAIN: "先用通用模电知识解释术语或原理，再按需举例。不要编造教材章节和页码。",
    INTENT_STUDY: (
        "按学生的问题给出课程概览或学习顺序与练习建议。"
        "没有课程大纲时，把内容说明为通用建议，不要编造本课程的章节和安排。"
    ),
    INTENT_ANALYZE: (
        "依据题干和对话中的已知条件分析电路。说明公式适用条件。"
        "缺少会影响结论的信息时，提出一句具体追问。"
    ),
}

LOCATE_PROMPT = """你是 Aurora 模电助教。只用提供的片段定位用户指定的资料。
核对资料名称、题号或主题，只报告片段来源中明确给出的章节和页码。
相似片段不能证明找到了指定资料，证据不足时说明未找到可核对的出处。"""


def _answer_direct(question: str, history: list[dict], intent: str) -> str | None:
    messages = [
        {"role": "system", "content": "你是 Aurora 模电助教。用中文回答。"
         + DIRECT_PROMPTS[intent]},
        *history,
        {"role": "user", "content": question},
    ]
    response = get_client().chat.completions.create(
        model=DEEPSEEK_MODEL, messages=messages, temperature=0.3,
    )
    return response.choices[0].message.content


def fallback_answer(question: str) -> str:
    """优雅降级：诚实说明「暂不支持」，并给出学习路径（不检索、不装懂）。

    降级的价值（面试考点）：一个 agent 最忌讳「硬编一个答案」。宁可明确说
    「我不会」，也比给出一个看起来自信但错误的结果强。
    """
    return (
        f"你问的「{question}」超出了 Aurora 当前的能力范围。\n\n"
        "Aurora 目前能帮你：\n"
        "1. 集成运放运算电路（比例/求和/积分/微分）的数值计算与分析；\n"
        "2. 负反馈四种组态的判断；\n"
        "3. 直流稳压电源的组成与工作原理；\n"
        "4. 模拟电子技术基础概念问答。\n\n"
        "建议学习路径：先复习第 6 章集成运放（虚短虚断 → 基本运算电路），"
        "再进入第 7 章反馈（组态判断 → 深度负反馈），最后第 9 章直流稳压电源。"
    )


def run(question: str, store: VectorStore | None = None, top_k: int = 3,
        *, session: Session | None = None) -> dict:
    """电路分析智能体的统一入口：上下文改写 → Router 分流 → 分派 → 汇总结果。

    返回一个 dict，统一了不同链路各异的中间产物，方便 demo 和评测取用：
        answer        最终回答
        circuit_type  Router 判出的类型标签
        intent        Router 判出的主意图（有效时提供）
        clarification  题目缺少关键条件时的具体追问（有值时提供）
        confidence    分类置信度
        reason        分类理由
        standalone    改写后的独立问题（第一轮 = 原问题）
        trace         opamp 链路的工具调用过程（其它链路为 None）
        hits          RAG 链路的检索命中（opamp/fallback 为 None）

    session：可选的多轮会话状态。同一个 Session 连着传两轮，历史自动累积。
             不传就是单轮，行为和改造前完全一致（旧脚本一行都不用改）。

    为什么 session 是 keyword-only（那个裸 `*`）：现有调用方都是
    `run(question, store)` 按位置传参，如果把 session 插在 store 之前，
    store 会被当成 session 吃掉。
    """
    session = session if session is not None else Session()
    history = session.context()

    # 一次改写、两处消费：Router 拿独立问题分类（不读整段历史，避免被上一轮
    # 的类型拽着走），检索拿独立问题做 query（"那""它"才能还原）。
    # 生成侧仍然用学生的原话 + history —— 改写有损，不能让它污染答案。
    standalone = rewrite_question(question, history)
    route_info = route(standalone)
    circuit_type = route_info["circuit_type"]
    intent = route_info.get("intent")
    clarification = route_info.get("clarification")
    trace = hits = None

    if intent == INTENT_OUT_OF_SCOPE or (intent is None and circuit_type == TYPE_FALLBACK):
        answer = fallback_answer(question)
    elif intent == INTENT_ANALYZE and clarification:
        answer = clarification
    elif intent in {INTENT_EXPLAIN, INTENT_STUDY}:
        answer = _answer_direct(question, history, intent)
    elif intent == INTENT_LOCATE:
        if store is None or not len(store):
            answer, hits = "当前没有可用的资料索引，暂时无法核对出处。", []
        else:
            answer, hits = ask_rag(
                question, store, top_k=top_k, history=history,
                query=standalone, system_prompt=LOCATE_PROMPT,
            )
    elif circuit_type == TYPE_OPAMP:
        # 只有运放链路挂电压校验；历史只传纯文本对（见 run_agent 的说明）
        answer, trace = run_agent(question, validators=OPAMP_VALIDATORS, history=history)
    elif intent == INTENT_ANALYZE and circuit_type not in RAG_PROMPTS:
        answer = _answer_direct(question, history, intent)
    else:
        # feedback / power / concept 共用同一套检索能力，只换「怎么引导模型」
        # （查表拿提示词，concept 不在表里 → None → 用 rag.py 的默认提示词）
        answer, hits = ask_rag(
            question, store, top_k=top_k,
            history=history,
            query=standalone,
            system_prompt=RAG_PROMPTS.get(circuit_type),
        )
    # 所有成功分支统一写回。
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("模型没有返回有效内容")
    # 注意写回的是学生的**原话**，不是 standalone —— 改写是内部中间产物，
    # 对话记录里只该有人真正说过的话。
    session.add_turn(question, answer)
    return {**route_info, "standalone": standalone,
            "answer": answer, "trace": trace, "hits": hits}
