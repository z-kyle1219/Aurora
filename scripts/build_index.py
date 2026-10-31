"""离线建库脚本 —— 把课件变成向量库（RAG 的第一步）。

跑一次即可：读取所有课件 md → 切块 → 向量化 → 存入 data/index/。

用法（在 aurora 目录下）：
    uv run scripts/build_index.py
"""

import sys
from pathlib import Path

# 用脚本自身位置定位路径，避免依赖「当前工作目录」在哪
AURORA_ROOT = Path(__file__).resolve().parent.parent          # aurora/
COURSEWARE_DIR = AURORA_ROOT.parent / "courseware_md"         # 课件在项目上一级
CARDS_FILE = AURORA_ROOT / "knowledge" / "formula_cards.md"   # 公式卡片（教材复现）
INDEX_DIR = AURORA_ROOT / "data" / "index"                    # 向量库落盘位置

from aurora.chunker import chunk_file
from aurora.card_loader import load_cards
from aurora.embedder import encode_passages
from aurora.vector_store import VectorStore


def main() -> int:
    md_files = sorted(COURSEWARE_DIR.glob("*.md"))
    if not md_files:
        print(f"没找到课件 md，请确认目录存在：{COURSEWARE_DIR}")
        return 1

    print(f"发现 {len(md_files)} 个课件 md，开始切块…\n")

    # 第一步：切块
    all_chunks = []
    for f in md_files:
        chunks = chunk_file(f)
        all_chunks.extend(chunks)
        print(f"  {f.name:<30} -> {len(chunks):>4} 块")

    # 第一步补：公式卡片（教材复现，第二层知识库，与课件块混入同一向量库）
    if CARDS_FILE.exists():
        cards = load_cards(CARDS_FILE)
        all_chunks.extend(cards)
        print(f"  [公式卡片] {CARDS_FILE.name:<24} -> {len(cards):>4} 块")
    else:
        print(f"  未找到公式卡片 {CARDS_FILE.name}，跳过")

    print(f"\n共 {len(all_chunks)} 块，开始向量化（首次会自动下载模型，稍等）…")

    # 第二步：批量向量化（一次传一批，比逐条调用快很多）
    vectors = encode_passages([c.text for c in all_chunks])
    print(f"向量化完成，维度 = {vectors.shape[1]}（每块文字被压成 {vectors.shape[1]} 个数字）\n")

    # 第三步：存库
    store = VectorStore(chunks=all_chunks, vectors=vectors)
    store.save(INDEX_DIR)
    print(f"已存入 {INDEX_DIR}\n")

    # 自检：拿库里的第一块去检索，看能否精确命中它自己（分数应≈1）。
    # 这验证了「切块 → 向量化 → 检索」整条链路是对的。
    # 两个注意点：
    #   1. probe 是「库里的内容」，要用 encode_passages（不加查询前缀），
    #      才能和库里的向量落在同一个空间里比较；
    #   2. 用「完整文本」而非截断，否则拿一小段去找全文，分数自然偏低。
    probe = encode_passages([all_chunks[0].text])[0]
    top = store.search(probe, top_k=1)
    print("自检：用库里第一块内容检索，命中结果——")
    print(f"  相似度 {top[0].score:.4f}（≈1 说明链路正确）")
    print(f"  来源   {top[0].chunk.source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
