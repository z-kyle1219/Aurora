"""LLM-as-judge —— 让模型当裁判，给另一个模型的回答打分。

这是「可评测」的进阶手段，解决关键词命中的局限：

    关键词命中是「机械的字面匹配」——「近似等电位」语义对但字面不含
    「电位相等」，就被误判为错；反过来，回答里碰巧含关键词、结论却是
    错的，又会漏判。LLM-as-judge 是让一个模型读「标准答案 + 回答」，
    理解语义后给出结构化判分，两种误判都能缓解。

和 Router 一样，判分也要「结构化」：用 function calling 强制裁判返回
{judgment, reason} 这种字段，而不是一段自由文本——这样评测脚本能直接
统计「对/部分对/错」的数量，而不是再去解析一句话。

为什么裁判能用同一个 DeepSeek？成本和一致性考虑：DeepSeek 便宜、调用
快，且和「考生」同一套模型接口，接入成本为零。代价是「自己人当裁判」
可能偏宽松——这是已知取舍，写清判分标准能缓解。
"""

import json

from aurora.config import DEEPSEEK_MODEL, get_client

# 答案判分的三个档位
JUDGMENTS = ["correct", "partial", "wrong"]

_JUDGE_PROMPT = """你是一位严格的电路助教评测裁判。

我会给你「标准答案」和「Aurora 助教」的回答，请判断 Aurora 的回答是否正确。

判分标准：
- correct：核心结论与标准答案一致；
- partial：方向正确，但结论不准确或不完整；
- wrong：结论错误，或避而不答、答非所问。

只调用 judge 工具，不要输出其它文字。"""

_JUDGE_TOOL = {
    "type": "function",
    "function": {
        "name": "judge",
        "description": "判断 Aurora 的回答是否正确",
        "parameters": {
            "type": "object",
            "properties": {
                "judgment": {
                    "type": "string",
                    "enum": JUDGMENTS,
                    "description": "判分：correct / partial / wrong",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话说明判分理由",
                },
            },
            "required": ["judgment"],
        },
    },
}

_HALLUCINATION_PROMPT = """你是一位严格的电路助教评测裁判，负责检查「幻觉」（编造）。

我会给你「检索到的课件片段」和「Aurora 助教」的回答。
请判断回答中的电路事实、公式、结论，是否出现了「片段里找不到依据」的编造。

判分标准：
- hallucinated = false：回答严格基于片段，没有编造片段之外的具体结论；
- hallucinated = true：回答出现了片段之外的具体公式、数据或结论（编造）。

注意：回答「诚实说明片段不足」不算幻觉，反而是正确的；只有「硬编了片段里
没有的内容」才算幻觉。只调用 judge 工具，不要输出其它文字。"""

_HALLUCINATION_TOOL = {
    "type": "function",
    "function": {
        "name": "judge",
        "description": "判断回答是否编造了检索片段之外的内容",
        "parameters": {
            "type": "object",
            "properties": {
                "hallucinated": {
                    "type": "boolean",
                    "description": "是否存在编造（true=有幻觉，false=无幻觉）",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话说明判断理由",
                },
            },
            "required": ["hallucinated"],
        },
    },
}


def _call_judge(system_prompt: str, tool: dict, user_content: str,
                field: str, default) -> dict:
    """通用的「调用裁判」入口：发 prompt + tool，解析返回的结构化字段。

    两个判分（正确性 / 幻觉）共用这一套「发请求 → 强制调工具 → 解析」，
    只有 prompt、tool schema、要取的字段不同，所以抽成这个私有函数。
    """
    client = get_client()
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "judge"}},
        temperature=0.0,
    )
    msg = resp.choices[0].message
    if not msg.tool_calls:
        return {"judgment": default, "reason": "裁判未返回有效判分"}
    try:
        args = json.loads(msg.tool_calls[0].function.arguments)
    except json.JSONDecodeError:
        return {"judgment": default, "reason": "裁判参数解析失败"}
    return {"judgment": args.get(field, default), "reason": args.get("reason", "")}


def judge_answer(standard_answer: str, model_answer: str) -> dict:
    """判断回答是否正确。返回 {judgment: correct/partial/wrong, reason}。"""
    result = _call_judge(
        _JUDGE_PROMPT,
        _JUDGE_TOOL,
        f"【标准答案】\n{standard_answer}\n\n【Aurora 回答】\n{model_answer}",
        field="judgment",
        default="partial",
    )
    if result["judgment"] not in JUDGMENTS:
        result["judgment"] = "partial"
    return result


def judge_hallucination(context: str, model_answer: str) -> dict:
    """判断回答是否编造了检索片段之外的内容。返回 {judgment: bool, reason}。

    注意：这里复用了 _call_judge 的通用结构，但返回的字段是布尔值
    （hallucinated），而不是三档判分。
    """
    result = _call_judge(
        _HALLUCINATION_PROMPT,
        _HALLUCINATION_TOOL,
        f"【检索片段】\n{context}\n\n【Aurora 回答】\n{model_answer}",
        field="hallucinated",
        default=False,
    )
    if not isinstance(result["judgment"], bool):
        result["judgment"] = False
    return {"hallucinated": result["judgment"], "reason": result["reason"]}
