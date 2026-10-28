"""RAG 学伴演示 —— 在线问答（先建库，再跑这个）。

用法（在 aurora 目录下）：
    uv run scripts/build_index.py   # 先建库（只需一次）
    uv run scripts/demo_rag.py      # 再跑问答
"""

from pathlib import Path

from aurora.rag import ask_rag
from aurora.vector_store import VectorStore

# 向量库位置（build_index.py 存盘的目录）
INDEX_DIR = Path(__file__).resolve().parent.parent / "data" / "index"

# 覆盖不同章节的测试问题，用来观察「检索 + 回答」是否都命中正确章节
QUESTIONS = [
    "什么是虚短和虚断？",
    "负反馈有哪四种组态？",
    "要稳定输出电压，应该选择哪种反馈组态？",
    "直流稳压电源由哪几个部分组成？",
]


def main() -> None:
    store = VectorStore.load(INDEX_DIR)
    print(f"已加载向量库，共 {len(store)} 个片段")
    print("=" * 70)

    for q in QUESTIONS:
        print(f"\n[问题] {q}\n")

        answer, hits = ask_rag(q, store, top_k=3)

        print("  [检索到的片段]")
        for i, h in enumerate(hits, 1):
            print(f"    {i}. 相似度 {h.score:.3f} | 来源 {h.chunk.source}")
            print(f"       预览: {h.chunk.text[:50]}…")

        print(f"\n  [Aurora 回答]\n{answer}")
        print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
