"""W3 电路分析智能体 —— function calling 调用回环。

这是 W3 的核心考点「tool use」的落地：把工具定义放进 tools 参数，
让模型自己「点菜」，我们的代码负责「上菜」（执行）并把结果回传，
直到模型给出最终回答。

一个回环的完整往返（对应上一轮讲的那张图）：
    ① 把 messages + tools 发给模型
    ② 模型返回 tool_calls（点菜，但没执行）
    ③ 我们的代码真正执行工具（上菜）
    ④ 把结果作为 tool 角色消息回传
    ⑤ 模型读到结果，继续生成；若还要算就回到②，否则给出最终回答

与 W2 的 RAG 最大的区别：RAG 是「把知识喂给模型」，tool use 是
「把能力交给模型」——一个补知识、一个补动作。
"""

import json

from aurora.config import DEEPSEEK_MODEL, get_client
from aurora.tools import TOOLS, execute_tool

SYSTEM_PROMPT = """你是「Aurora」，一位严谨的模拟电子技术课程助教，专长是电路分析。

你的任务是：识别电路类型 → 给出分析思路和正确结果。

请按以下结构组织你的回答（结构化报告）：
1. 电路类型：判断这是哪类电路（例如反相比例运算电路）；
2. 分析判据：列出你用到的依据（例如虚短、虚断）；
3. 数值计算：得出具体数值；
4. 最终结论：一句话给出答案。

关于数值计算，铁律：
- 绝对不要心算任何数值，凡涉及算术必须调用 calculate_expression 工具；
- 工具返回的结果就是标准答案，直接引用，不要自己重算或改动；
- 如果工具结果附带【合理性校验】警告（例如结果超出电源电压），
  必须按警告修正结论（例如运放饱和、输出被钳位）。

用中文回答，保持引导式教学风格，但计算题必须给出确定的数值结果。"""


def run_agent(question, tools=TOOLS, validators=None, max_rounds=5, temperature=0.3):
    """跑一次带工具调用的 agent 回环，返回 (最终回答, 过程记录)。

    validators: 可选，{工具名: 校验函数}。校验函数接收工具返回的字符串，
                返回一条警告字符串或 None。用于实现「校验回环」。

    过程记录（trace）是给演示和评测用的：把「模型点了什么菜、我们上了
    什么菜、有没有触发校验」都记下来，这正是「可评测解题流水线」要的东西。
    """
    client = get_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    trace = []

    for _ in range(max_rounds):
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            tools=tools,
            temperature=temperature,
        )
        choice = resp.choices[0]
        msg = choice.message

        # 模型请求了工具 → 执行工具，把结果回传后进入下一轮
        if msg.tool_calls:
            # 把「点菜」记录回 messages（assistant 角色，含 tool_calls）
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # 逐个「上菜」：执行工具 → 结果（含校验警告）作为 tool 角色消息回传
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                result = execute_tool(name, arguments)

                validator = (validators or {}).get(name)
                warning = validator(result) if validator else None
                content = f"{result}\n[合理性校验] {warning}" if warning else result

                trace.append({
                    "tool": name,
                    "arguments": arguments,
                    "result": result,
                    "warning": warning,
                })
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})

        # 模型直接返回文本 → 最终回答
        elif msg.content:
            return msg.content, trace

        # 既没调工具、也没生成内容：区分「长度截断」与其它异常
        elif choice.finish_reason == "length":
            raise RuntimeError("模型输出达到长度限制")

        else:
            raise RuntimeError("模型没有返回有效内容")

    return "（已达到最大工具调用轮数，仍未得到最终回答）", trace
