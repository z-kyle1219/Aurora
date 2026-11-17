"""W4 下半场 —— 上下文改写（指代消解 / condense question）。

这一层解决的是「多轮里代词还原」的问题：

    第一轮：反相比例电路 R1=10k，Rf=100k，ui=-3V，求 uo。   → uo = -30V
    第二轮：那 R1 换成 20k 呢？

第二轮这句话里没有「运放」「反相」「增益」任何字眼。直接拿去检索，向量库里
找不到东西；直接拿去分类，Router 只能靠猜。所以要先把它「补全」成一句不依赖
上文也能看懂的话：

    反相比例运算电路 R1 换成 20kΩ 时，求输出电压 uo。

三个设计约束（都很重要，别改）：

1. **放在 Router 之前**。改写只需要做一次，Router 和检索同时受益 ——
   Router 不直接读整段历史，避免历史把分类往上一轮的类型上拽；检索也拿到了
   完整 query。这是「一次改写、两处消费」。

2. **改写结果只走查询侧，不进生成侧**。生成回答仍然用学生的原始追问 + 历史。
   因为改写是有损的（模型可能把 20k 抄成 10k），让它污染答案就是灾难。
   它污染检索，最坏是查到错资料；污染生成，就是拿着错数值去算。

3. **不进 Session 历史**。「不是人说的话，就不该进 messages」—— 学生的原话
   才是对话记录，改写只是内部中间产物。

调用成本：每轮多一次模型调用（history 为空时直接跳过，第一轮零成本）。
用 temperature=0 保证同一句话每次改写结果稳定，方便评测。
"""

from aurora.config import DEEPSEEK_MODEL, get_client

# 只取最近 2 轮（= 4 条消息）喂给改写模型：
# 指代几乎都是紧邻上一轮的，塞更早的历史只会让改写跑偏，还白烧 token。
DEFAULT_RECENT_TURNS = 2

# 单条历史消息截断长度：助手的回答里有大段公式和来源标注，全塞进去没必要
MAX_HISTORY_CHARS = 300

# 改写结果超过这个长度，基本可以判定模型没按「只输出一句话」的要求做
MAX_REWRITE_CHARS = 500

REWRITE_SYSTEM_PROMPT = """你是「Aurora」电路助教的上下文改写器。

你的唯一任务：把学生最新说的那一句话，改写成一句**不依赖上文也能独立看懂**的问题。
注意：你是在改写问题，不是在回答问题。

规则：
1. 只补全指代（那、它、这个）和省略（主语、条件），不要回答问题，不要给出解法；
2. 严禁改动原句里的数值、元件名、单位（10kΩ 还是 10kΩ，不能变 20k 也不能省略）；
3. 不要补充对话历史里没有出现过的信息，宁可少补也不要瞎编；
4. 如果最新这句本身已经完整（比如直接问「负反馈有哪四种组态」），原样输出即可；
5. 只输出改写后的这一句话，不要解释、不要加引号、不要分点。"""

REWRITE_USER_TEMPLATE = """【对话历史】
{history_text}

【学生最新的一句话】
{question}

请把「学生最新的一句话」改写成独立问题。"""


def _format_history(history: list[dict], max_turns: int) -> str:
    """把最近的对话历史压成「学生：… / Aurora：…」的文本块。

    只保留最近 max_turns 轮，单条过长则截断 —— 改写只需要紧邻的上下文。
    """
    recent = history[-(max_turns * 2):]
    lines = []
    for msg in recent:
        speaker = "学生" if msg.get("role") == "user" else "Aurora"
        text = (msg.get("content") or "").strip().replace("\n", " ")
        if len(text) > MAX_HISTORY_CHARS:
            text = text[:MAX_HISTORY_CHARS] + "…"
        lines.append(f"{speaker}：{text}")
    return "\n".join(lines)


def rewrite_question(question: str, history: list[dict] | None = None,
                     max_turns: int = DEFAULT_RECENT_TURNS,
                     temperature: float = 0.0) -> str:
    """把当前的追问改写成不依赖上文的独立问题。

    history 为空（第一轮）时直接返回原问题，不调用模型 —— 这是天然的门控：
    只有追问才付这一次调用。

    任何异常情况（模型返回空、返回太长、返回一段解题过程）都退回原问题，
    保证这一层「只会让结果变好，不会让流程崩掉」。
    """
    if not history:
        return question

    history_text = _format_history(history, max_turns)
    if not history_text:
        return question

    client = get_client()
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": REWRITE_USER_TEMPLATE.format(
                    history_text=history_text, question=question
                ),
            },
        ],
        temperature=temperature,
    )

    standalone = (resp.choices[0].message.content or "").strip().strip('"“”\'')
    # 防御：空回答、或者模型没听话写了一大段，都退回原问题
    if not standalone or len(standalone) > MAX_REWRITE_CHARS:
        return question
    return standalone
