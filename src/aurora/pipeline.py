"""W3 电路分析智能体 —— 编排层（Router 分流 + 各链路分派）。

这是把上一轮写好的三块能力（RAG、tool use、校验回环）真正「串成一个智能体」
的地方：入口只有一个 run()，它先问 Router「这是哪类电路」，再按标签分派。

分层回顾（面试常问的「能力层 vs 编排层」）：
    - 能力层：rag.py（检索）、tools.py（算数+校验）、router.py（分类）
      每一块都只做一件事、都能单独测试；
    - 编排层：本文件，负责「决策」——拿到类型标签后走哪条路、挂什么校验。

五条链路：
    opamp    → run_agent（tool use + 电压校验回环，校验器在这里自动挂上）
    feedback → ask_rag + 决策树提示词
    power    → ask_rag + 链式流程提示词
    concept  → ask_rag + 默认学伴提示词
    fallback → 模板直接给「暂不支持 + 学习路径」（不检索、不装懂）
"""

from aurora.agent import run_agent
from aurora.rag import ask_rag
from aurora.router import (
    TYPE_FALLBACK,
    TYPE_FEEDBACK,
    TYPE_OPAMP,
    TYPE_POWER,
    route,
)
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


def run(question: str, store: VectorStore | None = None, top_k: int = 3) -> dict:
    """电路分析智能体的统一入口：Router 分流 → 分派 → 汇总结果。

    返回一个 dict，统一了不同链路各异的中间产物，方便 demo 和评测取用：
        answer        最终回答
        circuit_type  Router 判出的类型标签
        confidence    分类置信度
        reason        分类理由
        trace         opamp 链路的工具调用过程（其它链路为 None）
        hits          RAG 链路的检索命中（opamp/fallback 为 None）
    """
    route_info = route(question)

    if route_info["circuit_type"] == TYPE_OPAMP:
        answer, trace = run_agent(question, validators=OPAMP_VALIDATORS)
        return {**route_info, "answer": answer, "trace": trace, "hits": None}

    if route_info["circuit_type"] == TYPE_FEEDBACK:
        answer, hits = ask_rag(question, store, top_k=top_k, system_prompt=FEEDBACK_PROMPT)
        return {**route_info, "answer": answer, "trace": None, "hits": hits}

    if route_info["circuit_type"] == TYPE_POWER:
        answer, hits = ask_rag(question, store, top_k=top_k, system_prompt=POWER_PROMPT)
        return {**route_info, "answer": answer, "trace": None, "hits": hits}

    if route_info["circuit_type"] == TYPE_FALLBACK:
        return {**route_info, "answer": fallback_answer(question), "trace": None, "hits": None}

    # 其余（concept / 未知）→ 学伴 RAG
    answer, hits = ask_rag(question, store, top_k=top_k)
    return {**route_info, "answer": answer, "trace": None, "hits": hits}
