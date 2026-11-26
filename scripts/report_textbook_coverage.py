"""生成逐章逐节覆盖清单（data/index-textbook/COVERAGE.md）。

数据源只有一处：`data/index-textbook/build_report.json`（由 build_textbook_index.py
写入）。这样覆盖清单不会和索引实际内容脱节 —— 重建索引后重跑本脚本即可。

用法（在 aurora 目录下）：
    uv run scripts/report_textbook_coverage.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AURORA_ROOT = Path(__file__).resolve().parent.parent
REPORT = AURORA_ROOT / "data" / "index-textbook" / "build_report.json"
OUT = REPORT.with_name("COVERAGE.md")

# 目录里没有教学正文、或按设计不入库的条目，在清单末尾单列，写明排除理由
EXCLUSIONS = [
    ("封面 / 版权页 / 第六版至初版序（PDF 第 1-13 页）", "非教学内容；版次与出版信息从封面与序言人工核对"),
    ("目录（印刷前置页，PDF 第 17-21 页）", "只用于核对章节覆盖与页码，不作为检索语料"),
    ("本书常用符号表（PDF 第 14-16 页）", "符号表以表格为主，OCR 后列错位严重；符号含义在各章正文首次出现处保留"),
    ("参考文献（P556）", "非教学内容"),
    ("索引（汉英对照，P557-565）", "检索工具页，非教学内容；且会与正文术语重复"),
]


def _chapter_label(row: dict) -> str:
    ch = row["chapter"]
    return f"第{ch}章" if ch.isdigit() else ch


def main() -> int:
    if not REPORT.exists():
        print(f"缺少 {REPORT}，请先运行 uv run scripts/build_textbook_index.py", file=sys.stderr)
        return 1
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = data["coverage_rows"]
    counts = data["chunk_counts"]
    book = data["book"]

    lines: list[str] = []
    lines.append("# 教材逐章逐节覆盖清单")
    lines.append("")
    lines.append("> 本文件由 `scripts/report_textbook_coverage.py` 从 "
                 "`data/index-textbook/build_report.json` 生成，**不要手改**；")
    lines.append("> 重建索引后重跑该脚本即可刷新。")
    lines.append("")
    lines.append(f"- 教材：{book['title']}（{book['pdf_pages']} 个 PDF 页 / {book['printed_pages']} 个印刷页）")
    lines.append(f"- 页码口径：PDF 页序 = 印刷页码 + {book['page_offset']}")
    lines.append(f"- 入库规模：教材 {counts['textbook']} 块 + 课件 {counts['courseware']} 块 + "
                 f"公式卡片 {counts['cards']} 块 = 共 {counts['total']} 块")
    lines.append("")
    lines.append("状态词表：`已入库` / `已入库·仅节引言`（正文在各小节）/ "
                 "`已入库·无独立正文`（标题后直接进下一节）/ `已入库·文本偏少`（以图为主）/ "
                 "`已入库·标题未识别`（按页码范围取文）/ `未入库·无正文` / `仅作答案来源`。")
    lines.append("")

    order: list[str] = []
    for row in rows:
        if row["chapter"] not in order:
            order.append(row["chapter"])

    for ch in order:
        sub = [r for r in rows if r["chapter"] == ch]
        title = sub[0]["title"] if sub[0]["no"] == ch else ch
        lines.append(f"## {_chapter_label(sub[0])} {title if sub[0]['no'] == ch else ''}".rstrip())
        lines.append("")
        lines.append("| 编号 | 小节 | 印刷页 | 字数 | 块数 | 丢弃行 | 状态 | 备注 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in sub:
            pages = f"P{r['start_page']}" if r["start_page"] == r["end_page"] else \
                    f"P{r['start_page']}–{r['end_page']}"
            note = (r["note"] or "").replace("|", "/")
            lines.append(f"| {r['no']} | {r['title']} | {pages} | {r['chars']} | {r['chunks']} | "
                         f"{r['dropped']} | {r['status']} | {note} |")
        lines.append("")

    lines.append("## 未入库条目（按设计排除）")
    lines.append("")
    lines.append("| 条目 | 处理 | 理由 |")
    lines.append("|---|---|---|")
    for item, reason in EXCLUSIONS:
        lines.append(f"| {item} | 不入库 | {reason} |")
    lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"已写入 {OUT}（{len(rows)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
