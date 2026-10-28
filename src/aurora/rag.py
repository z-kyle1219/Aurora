"""RAG 学伴 —— 检索增强生成（把离线建库和在线问答串起来）。

这是 W2 的核心成果：学伴从「凭记忆回答」升级为「引用课件回答」。

三步，正好对应 RAG 三个字母：
    R（Retrieve 检索）  encode_query(问题) -> store.search(top_k)
    A（Augment 增强）   把命中的片段拼成上下文，塞进 prompt
    G（Generate 生成）  让 DeepSeek 基于上下文回答

「增强」的本质，就是你之前理解的「开卷考试」：模型本来不知道课件内容，
我们把相关内容抄进它的输入，它就「被增强」了知识。

抑制幻觉的三个手段（面试高频考点，这里都落到了代码里）：
    1. 提示词里明确要求「只能基于给定片段回答」；
    2. 要求引用出处（来源文件），答句可追溯、可验证；
    3. 片段不足时诚实说「没检索到」，而不是硬编一个答案。
"""

from aurora.config import DEEPSEEK_MODEL, get_client
from aurora.embedder import encode_query
from aurora.vector_store import SearchResult, VectorStore

# RAG 系统提示词：和 v0 学伴的区别在于「引入了课件片段」这一新输入
RAG_SYSTEM_PROMPT = """你是「Aurora」，一位耐心的模拟电子技术课程助教。

现在我会给你几段从课程课件里检索出来的相关内容，用【课件片段】标记，并注明来源文件。

回答规则：
1. 只能基于我提供的课件片段回答，不要用片段之外的知识补充电路细节；
2. 回答时，在相关句子末尾用括号注明来源，例如「（来源：7.2 反馈组态.md）」；
3. 如果提供的片段不足以回答这个问题，直接说「课件中暂未检索到相关内容」，不要编造；
4. 用中文，保持你引导式、讲清楚「为什么」的教学风格。"""


def retrieve(store: VectorStore, question: str, top_k: int = 3) -> list[SearchResult]:
    """第一步 R（检索）：把问题向量化，取出最相关的 top_k 个片段。

    注意这里用的是 encode_query（带查询前缀），而建库时用的是 encode_passages
    （不带前缀）——这正是 BGE 的推荐用法：问题加前缀、文档不加，检索更准。
    """
    query_vector = encode_query(question)
    return store.search(query_vector, top_k=top_k)


def build_context(hits: list[SearchResult]) -> str:
    """第二步 A（增强）的一部分：把命中片段拼成一段上下文文本。"""
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(f"【课件片段 {i}】（来源：{h.chunk.source}）\n{h.chunk.text}")
    return "\n\n".join(parts)


def ask_rag(question: str, store: VectorStore, top_k: int = 3,
            history: list[dict] | None = None) -> tuple[str, list[SearchResult]]:
    """完整的 RAG 问答：检索 -> 拼上下文 -> 生成。返回 (回答, 命中的片段)。

    之所以把「命中的片段」也返回，是为了让调用方（demo / 界面）能把「检索到
    了什么」展示给用户看——这也是「可解释性」，评测时能用来算「引用命中率」。
    """
    hits = retrieve(store, question, top_k=top_k)
    if not hits:
        return "课件库还是空的，请先运行 build_index.py 建库。", []

    context = build_context(hits)
    user_message = (
        f"【课件片段】\n{context}\n\n"
        f"【学生问题】\n{question}"
    )

    client = get_client()
    messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,  # 基于资料回答，降低随机性，更忠实于片段（抑制幻觉）
    )
    answer = resp.choices[0].message.content
    return answer, hits
