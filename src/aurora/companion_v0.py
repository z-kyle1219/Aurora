"""电路学伴 v0 —— 纯提示词版。

这是整个项目最小、最核心的一块：不检索课件、不调用工具、没有循环，
就是「系统提示词 + 用户问题 → 模型回答」。

先跑通它，是为了建立三个最基本的认知：

1. LLM 的输入只有「消息列表」（messages），系统提示词决定它的角色；
2. 所谓「智能体」，本质就是不断构造 messages 再调模型，没有魔法；
3. 好的提示词能显著影响回答质量——后面做的 RAG、工具调用，都是在
   帮模型补足「知识 / 能力」的缺口，而不是改变「调模型」这件事本身。

这也是为什么我们第一版不引入 LangChain：这里几十行代码就是它的本质。
"""

from aurora.config import DEEPSEEK_MODEL, get_client

# 系统提示词：定义 Aurora 的角色与教学风格
SYSTEM_PROMPT = """你是「Aurora」，一位耐心的模拟电子技术课程助教。

你的教学风格：
1. 引导式：先判断学生已经理解到哪一步，再针对性讲解，不要一上来倾倒全部答案；
2. 循序渐进：把复杂问题拆成小步骤，讲完一步确认一步；
3. 讲清楚「为什么」：不只给结论，要讲物理意义和推导思路；
4. 术语准确：严格使用课程中的标准术语和符号约定（如虚短、虚断、负反馈组态等）；
5. 用中文回答。

如果学生问的是一道计算题，先给出解题思路和方法，再引导他自己算，
不要直接报最终答案（除非他明确要求看完整解答）。"""


def ask(question: str, history: list[dict] | None = None) -> str:
    """向学伴提问，一次性返回完整回答文本。

    参数:
        question: 学生的问题
        history:  历史对话，格式与 OpenAI 一致 [{"role":..., "content":...}]
    """
    client = get_client()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.7,  # 学伴偏对话，稍高一点让语气更自然
    )
    return resp.choices[0].message.content


def ask_stream(question: str, history: list[dict] | None = None):
    """流式版，逐段返回文本（网页界面里会用，体验更接近真实聊天）。"""
    client = get_client()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    stream = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.7,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
