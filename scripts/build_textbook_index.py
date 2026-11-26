"""离线建库（教材版）—— 教材 + 课件 + 公式卡片 → data/index-textbook。

与 `build_index.py` 的关系：
- `build_index.py` 建的是默认索引 `data/index/`（课件 + 公式卡片），**保持不变**，
  在线问答仍按原命令加载它；
- 本脚本多合入一层「教材正文」（扫描版 OCR 后按章节切块），产出独立目录
  `data/index-textbook/`，两者磁盘格式相同，都由 `VectorStore.load()` 读取。

磁盘格式（与默认索引一致，不新增字段）：
    chunks.json   每块的 text / source / heading
    vectors.npy   (N, 512) float32，第 i 行对应 chunks.json 第 i 块

用法（在 aurora 目录下）：
    uv run scripts/ocr_textbook.py                  # 先 OCR（只需跑一次）
    uv run scripts/build_textbook_index.py          # 再建库
    uv run scripts/build_textbook_index.py --verify-only   # 只做加载自检
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

AURORA_ROOT = Path(__file__).resolve().parent.parent
TEXTBOOK_PDF = AURORA_ROOT.parent / "电子技术基础_模拟部分(第六版)_康华光.pdf"
COURSEWARE_DIR = AURORA_ROOT.parent / "courseware_md"
CARDS_FILE = AURORA_ROOT / "knowledge" / "formula_cards.md"
TOC_FILE = AURORA_ROOT / "data" / "textbook_toc.txt"
OCR_DIR = AURORA_ROOT / "data" / "textbook_ocr"
INDEX_DIR = AURORA_ROOT / "data" / "index-textbook"

from aurora.card_loader import load_cards
from aurora.chunker import chunk_file
from aurora.embedder import MODEL_NAME, encode_passages, get_model
from aurora.textbook_chunker import BOOK_TITLE, build_textbook_chunks
from aurora.vector_store import VectorStore


def _token_stats(texts: list[str]) -> dict:
    """用当前 Embedding 模型自己的 tokenizer 统计块长，确认没有被静默截断。

    bge-small-zh-v1.5 的 max_seq_length=512。超出部分 sentence-transformers 会
    直接切掉且不报错——检索时看起来「有这块」，实际后半段没进向量。这里先量出来。
    """
    model = get_model()
    tok = model.tokenizer
    limit = int(getattr(model, "max_seq_length", 512))
    lens: list[int] = []
    for t in texts:
        try:
            lens.append(len(tok.encode(t, add_special_tokens=True)))
        except TypeError:                      # 少数版本 tokenizer 不接受该参数
            lens.append(len(tok.encode(t)))
    over = [n for n in lens if n > limit]
    return {
        "limit": limit,
        "max": max(lens) if lens else 0,
        "mean": round(sum(lens) / len(lens), 1) if lens else 0,
        "over_limit": len(over),
        "over_limit_indices": [i for i, n in enumerate(lens) if n > limit][:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=INDEX_DIR)
    parser.add_argument("--max-chars", type=int, default=420)
    parser.add_argument("--problems-per-chapter", type=int, default=3)
    parser.add_argument("--verify-only", action="store_true", help="只加载已有索引做自检")
    args = parser.parse_args()

    if args.verify_only:
        return _verify(args.out)

    if not TOC_FILE.exists():
        print(f"缺少目录清单：{TOC_FILE}", file=sys.stderr)
        return 1
    pages = OCR_DIR / "pages"
    if not pages.exists() or not any(pages.glob("p*.txt")):
        print(f"缺少 OCR 文本：{pages}，请先运行 uv run scripts/ocr_textbook.py", file=sys.stderr)
        return 1

    print("[1/4] 教材切块（目录清单 + OCR 分页文本）…")
    textbook_chunks, rows, stats = build_textbook_chunks(
        TOC_FILE, pages, max_chars=args.max_chars,
        problems_per_chapter=args.problems_per_chapter,
    )
    print(f"      {len(textbook_chunks):>5} 块 / {len(rows)} 个目录条目")

    print("[2/4] 课件切块（沿用 chunker.chunk_file）…")
    md_files = sorted(COURSEWARE_DIR.glob("*.md"))
    if not md_files:
        print(f"      没找到课件 md：{COURSEWARE_DIR}（跳过）")
    courseware_chunks = []
    for f in md_files:
        got = chunk_file(f)
        courseware_chunks.extend(got)
        print(f"      {f.name:<32} -> {len(got):>3} 块")

    print("[3/4] 公式卡片（教材复现，沿用 card_loader）…")
    cards = load_cards(CARDS_FILE) if CARDS_FILE.exists() else []
    print(f"      {CARDS_FILE.name:<32} -> {len(cards):>3} 块")

    all_chunks = textbook_chunks + courseware_chunks + cards
    texts = [c.text for c in all_chunks]
    print(f"\n共 {len(all_chunks)} 块，开始向量化（本地 {MODEL_NAME}）…")

    tokens = _token_stats(texts)
    print(f"      token 上限 {tokens['limit']}，实测最长 {tokens['max']}，"
          f"均值 {tokens['mean']}，超限 {tokens['over_limit']} 块")

    vectors = encode_passages(texts)
    if vectors.shape[0] != len(all_chunks):
        print(f"向量行数 {vectors.shape[0]} != 片段数 {len(all_chunks)}", file=sys.stderr)
        return 1

    store = VectorStore(chunks=all_chunks, vectors=vectors)
    store.save(args.out)
    print(f"[4/4] 已写入 {args.out}（{len(store)} 块 × {vectors.shape[1]} 维）")

    # 自检：用库内第一块内容检索，分数应≈1（切块→向量化→检索整链正确）
    probe = encode_passages([all_chunks[0].text])[0]
    top = store.search(probe, top_k=1)[0]
    print(f"      自检：命中自身相似度 {top.score:.4f}（≈1 为正确）")

    report = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "index_dir": str(args.out),
        "book": {
            "title": BOOK_TITLE,
            "pdf": str(TEXTBOOK_PDF),
            "pdf_pages": 598,
            "printed_pages": 576,
            "page_offset": 22,
            "toc_source": str(TOC_FILE),
            "ocr_source": str(pages),
        },
        "embedding_model": MODEL_NAME,
        "chunk_counts": {
            "textbook": len(textbook_chunks),
            "courseware": len(courseware_chunks),
            "cards": len(cards),
            "total": len(all_chunks),
        },
        "vectors": {"rows": int(vectors.shape[0]), "dim": int(vectors.shape[1])},
        "tokens": tokens,
        "textbook_stats": stats,
        "coverage_rows": [r.as_dict() for r in rows],
        "commands": {
            "ocr": "uv run scripts/ocr_textbook.py",
            "build": "uv run scripts/build_textbook_index.py",
            "load": "uv run python -m aurora.api --index data/index-textbook",
        },
    }
    report_path = args.out / "build_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      报告：{report_path}")

    return _verify(args.out)


def _verify(index_dir: Path) -> int:
    """用现有 VectorStore.load() 加载并核对行数，确认服务可直接吃这个索引。"""
    try:
        store = VectorStore.load(index_dir)
    except (OSError, ValueError, TypeError, EOFError) as exc:
        print(f"加载失败：{index_dir}（{exc}）", file=sys.stderr)
        return 1
    rows = len(store.chunks) if store.chunks else 0
    vec_rows = 0 if store.vectors is None else int(store.vectors.shape[0])
    print(f"\n加载自检：VectorStore.load('{index_dir}') -> {rows} 块，向量 {vec_rows} 行")
    if rows != vec_rows:
        print("行数与片段数不一致", file=sys.stderr)
        return 1
    print("通过：向量行数与片段数一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
