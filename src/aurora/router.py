"""W3 下半场 —— Router（电路类型分流器）。

这是「电路分析智能体」的入口：拿到一个问题，先判断它属于哪类电路，
再把结果交给编排层（pipeline.py）分派到对应链路。

和上一轮 tool use 的区别：
    - tool use 是「给模型发工具让它算数」；
    - Router 是「让模型先给问题贴一个类型标签」，代码按标签走不同分支。
两者用的是同一套机制（function calling），只是模型返回的不是计算结果，
而是一个枚举标签——这也是 agent 里最经典的「Router」模式。

为什么用 LLM 分类而不是 if 关键词：
    关键词匹配覆盖不了自然语言的变体（「帮我看看这个运放能出多少伏」和
    「求 uo」是一回事），而 LLM 分类能理解语义。更关键的是——分类结果
    是一个可评测的标签，W4 能直接算「分类准确率」和混淆矩阵。
"""

import json

from aurora.config import DEEPSEEK_MODEL, get_client

# 五类电路标签（英文短标签，机器友好，评测时直接对比字符串）
TYPE_OPAMP = "opamp"        # 集成运放运算电路（数值计算，tool use）
TYPE_FEEDBACK = "feedback"  # 负反馈判断（决策树）
TYPE_POWER = "power"        # 直流稳压电源（链式流程）
TYPE_CONCEPT = "concept"    # 基础概念问答（学伴 RAG）
TYPE_FALLBACK = "fallback"  # 优雅降级（暂不支持 + 学习路径）

CIRCUIT_TYPES = [TYPE_OPAMP, TYPE_FEEDBACK, TYPE_POWER, TYPE_CONCEPT, TYPE_FALLBACK]

# 标签 → 中文名（给评测报告、界面展示用）
TYPE_LABELS = {
    TYPE_OPAMP: "集成运放运算电路",
    TYPE_FEEDBACK: "负反馈判断",
    TYPE_POWER: "直流稳压电源",
    TYPE_CONCEPT: "基础概念问答",
    TYPE_FALLBACK: "优雅降级",
}

# 默认：分类失败时兜底走「概念问答」（RAG 能兜住大多数问题）
DEFAULT_TYPE = TYPE_CONCEPT

ROUTE_SYSTEM_PROMPT = """你是「Aurora」电路分析智能体的分流器（Router）。

根据用户问题，判断它属于以下哪一类，并调用 route_circuit 工具返回标签：

- opamp：集成运放运算电路。涉及虚短/虚断、比例/求和/积分/微分运算、求输出电压的具体数值。
- feedback：负反馈判断。涉及判断负反馈组态（电压/电流 × 串联/并联）、深度负反馈 Af≈1/F、反馈对性能的影响。
- power：直流稳压电源。涉及变压器/整流/滤波/稳压的组成与工作原理。
- concept：基础概念问答。虚短虚断、电流源、放大器一般概念等，不属于上面三类的电路概念题。
- fallback：超出《模拟电子技术》课程范围，或无法归入以上四类（如数字电路、时序、软件等）。

判断规则：
- 需要算出具体输出电压数值、比例/求和运算 → opamp
- 问「选哪种反馈组态」「判断组态」「稳定电压/电流/输入电阻」→ feedback
- 问「稳压电源组成」「整流/滤波/稳压原理」→ power
- 一般概念、定义、原理 → concept
- 明显超纲 → fallback

只调用 route_circuit 工具，不要输出任何其它文字。"""

# 模型看到的「分类菜单」：一个只负责贴标签的工具
ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "route_circuit",
        "description": "判断用户问题属于哪类电路/知识点，返回类型标签与置信度",
        "parameters": {
            "type": "object",
            "properties": {
                "circuit_type": {
                    "type": "string",
                    "enum": CIRCUIT_TYPES,
                    "description": "电路类型标签，必须是枚举值之一",
                },
                "confidence": {
                    "type": "number",
                    "description": "判断的置信度，取值 0~1",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话说明判断理由（用于评测时人工核对）",
                },
            },
            "required": ["circuit_type", "confidence"],
        },
    },
}


def route(question: str, temperature: float = 0.0) -> dict:
    """给一个问题贴电路类型标签，返回 {circuit_type, confidence, reason}。

    用 tool_choice 强制模型调用 route_circuit，保证拿到结构化标签
    （而不是让模型自由发挥一段文字，那样还得再解析一次）。
    """
    client = get_client()
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": ROUTE_SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        tools=[ROUTE_TOOL],
        tool_choice={"type": "function", "function": {"name": "route_circuit"}},
        temperature=temperature,
    )

    msg = resp.choices[0].message
    # 防御：万一模型没按预期返回 tool_calls，兜底走概念问答
    if not msg.tool_calls:
        return {"circuit_type": DEFAULT_TYPE, "confidence": 0.0, "reason": "Router 未返回有效分类"}

    try:
        args = json.loads(msg.tool_calls[0].function.arguments)
    except json.JSONDecodeError:
        return {"circuit_type": DEFAULT_TYPE, "confidence": 0.0, "reason": "分类参数解析失败"}

    circuit_type = args.get("circuit_type", DEFAULT_TYPE)
    if circuit_type not in CIRCUIT_TYPES:
        circuit_type = DEFAULT_TYPE

    return {
        "circuit_type": circuit_type,
        "confidence": float(args.get("confidence", 0.0)),
        "reason": args.get("reason", ""),
    }
