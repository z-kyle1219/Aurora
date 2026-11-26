"""教材 OCR 预处理 —— 把扫描版教材变成「一页一个 txt」。

为什么需要这一步：
教材《电子技术基础·模拟部分》第六版是扫描件（Pdg2Pic 生成，598 页，
无文字层，get_text() 全为空），pypdf/markitdown 抽不出任何文字。要入库
只能先 OCR。本脚本用 pymupdf 渲染 300dpi 灰度页 + tesseract chi_sim 识别，
产出分页文本，供 textbook_chunker 按章节切块。

产物（默认 data/textbook_ocr/，属生成物，已被 .gitignore 忽略）：
    pages/p001.txt … p598.txt   逐页 OCR 文本（1-based，对应 PDF 页序）
    meta.json                   页码数、DPI、引擎版本、逐页字符数

页码口径：
    PDF 页序（1-based）= 印刷页码 + 22（本书实测：印刷 P39 落在 PDF 第 61 页）
    OCR 文本里同时保留页脚识别出的印刷页码，切块时以结构清单为准。

用法（在 aurora 目录下）：
    uv run scripts/ocr_textbook.py                 # 全量，断点续跑
    uv run scripts/ocr_textbook.py --pages 1-40     # 只跑前 40 页
    uv run scripts/ocr_textbook.py --force          # 忽略已有 txt 重跑

依赖：pymupdf（dev 依赖）、外部命令 tesseract（需 chi_sim 语言包）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AURORA_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = AURORA_ROOT.parent / "电子技术基础_模拟部分(第六版)_康华光.pdf"
DEFAULT_OUT = AURORA_ROOT / "data" / "textbook_ocr"

# 渲染分辨率：300dpi 是扫描件 OCR 的常用折中——更低会糊掉小字号公式，
# 更高只增加耗时（tesseract 内部还会再缩放）。
DPI = 300
LANG = "chi_sim"


def _tesseract_version() -> str:
    try:
        out = subprocess.run(["tesseract", "--version"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        first = (out.stdout or out.stderr).splitlines()[0]
        return first.strip()
    except (OSError, IndexError):
        return "unknown"


def _parse_pages(spec: str | None, total: int) -> list[int]:
    """把 "1-40,60" 解析成 1-based 页号列表；默认全部。"""
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.extend(range(int(lo), int(hi) + 1))
        else:
            pages.append(int(part))
    return [p for p in pages if 1 <= p <= total]


def _ocr_image(img: Path) -> str:
    """调用 tesseract 识别单张图，返回文本。--psm 3 是自动版面分析。

    OMP_THREAD_LIMIT=1：tesseract 默认用 OpenMP 吃满所有核，多进程并行时会出现
    「12 个实例各开 14 线程」的互相抢核，实测比单线程并行慢 4 倍以上。
    """
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    proc = subprocess.run(
        ["tesseract", str(img), "stdout", "-l", LANG, "--psm", "3"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract 失败（{img.name}）：{proc.stderr.strip()[:200]}")
    return proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pages", type=str, default=None, help="页范围，如 1-40,60")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--force", action="store_true", help="重跑已有 txt 的页")
    args = parser.parse_args()

    try:
        import pymupdf
    except ImportError:
        print("缺少 pymupdf。请先 `uv add --dev pymupdf`。", file=sys.stderr)
        return 1
    if shutil.which("tesseract") is None:
        print("未找到 tesseract 命令，请安装并确认 chi_sim 语言包可用。", file=sys.stderr)
        return 1
    if not args.pdf.exists():
        print(f"找不到教材 PDF：{args.pdf}", file=sys.stderr)
        return 1

    doc = pymupdf.open(args.pdf)
    total = doc.page_count
    wanted = _parse_pages(args.pages, total)

    pages_dir = args.out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    todo = wanted
    if not args.force:
        todo = [p for p in wanted if not (pages_dir / f"p{p:03d}.txt").exists()]
    print(f"教材共 {total} 页；本次待处理 {len(todo)} 页"
          f"（{args.workers} 线程，批 {args.batch} 页，{DPI}dpi，{LANG}）")

    started = time.time()
    char_counts: dict[str, int] = {}
    for start in range(0, len(todo), args.batch):
        batch = todo[start:start + args.batch]
        with tempfile.TemporaryDirectory(prefix="aurora_ocr_") as tmp:
            tmpdir = Path(tmp)
            images: list[tuple[int, Path]] = []
            for pno in batch:
                img = tmpdir / f"p{pno:03d}.png"
                # 灰度：体积约为彩色的 1/3，OCR 精度基本无损（原件是黑白扫描）
                doc[pno - 1].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY).save(str(img))
                images.append((pno, img))

            def work(item: tuple[int, Path]) -> tuple[int, str]:
                return item[0], _ocr_image(item[1])

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(work, images))

            for pno, text in results:
                text = text.replace("\r\n", "\n")
                (pages_dir / f"p{pno:03d}.txt").write_text(text, encoding="utf-8")
                char_counts[f"p{pno:03d}"] = len(text.strip())

        done = min(start + args.batch, len(todo))
        elapsed = time.time() - started
        rate = elapsed / done if done else 0
        print(f"  {done}/{len(todo)} 页  {rate:.2f}s/页  预计剩余 {rate * (len(todo) - done) / 60:.1f} 分钟")

    # meta.json 汇总：已有 txt 的页也统计进来，便于判断空页/缺页
    meta_path = args.out / "meta.json"
    counts: dict[str, int] = {}
    for f in sorted(pages_dir.glob("p*.txt")):
        counts[f.stem] = len(f.read_text(encoding="utf-8").strip())
    empty = [k for k, v in counts.items() if v < 20]
    meta = {
        "pdf": str(args.pdf),
        "pdf_pages": total,
        "ocr_pages": len(counts),
        "dpi": DPI,
        "lang": LANG,
        "engine": _tesseract_version(),
        "page_offset": 22,  # PDF 页序 = 印刷页码 + 22
        "empty_pages": empty,
        "char_counts": counts,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成：{len(counts)} 页文本 -> {pages_dir}")
    if empty:
        print(f"警告：{len(empty)} 页字符数 < 20，可能空白或识别失败：{', '.join(empty[:20])}")
    print(f"元数据：{meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
