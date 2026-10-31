"""公式卡片加载器 —— 把 formula_cards.md 解析成 Chunk 列表。

教材复现的「第二层知识库」：教材是扫描版（无文字层），公式无法 OCR，
改为「读图转写」成结构化卡片。每张卡 = 一个 Chunk，与课件自动切块混进
同一个向量库，`source` 以「公式卡片：」前缀区分（评测时可量化来源）。

卡片格式（固定字段，检索命中率高，机器可解析）：
    ## 标题（如「虚短虚断（理想运放）」）
    关键词：虚短、虚断、理想运放……
    公式：v_p - v_n ≈ 0；……
    解释：……
    条件：……
    来源：……
"""

import re
from pathlib import Path

from aurora.chunker import Chunk

# 一张卡以「## 」开头；其下各行是「字段：内容」，直到下一张卡或文件结束
_CARD_HEADING = re.compile(r"^##\s+(.+)$")


def load_cards(path: str | Path) -> list[Chunk]:
    """解析卡片文件，返回 list[Chunk]（每张卡一块）。"""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    chunks: list[Chunk] = []

    i = 0
    while i < len(lines):
        m = _CARD_HEADING.match(lines[i].strip())
        if not m:
            i += 1
            continue

        title = m.group(1).strip()
        i += 1
        body: list[str] = []
        while i < len(lines) and not _CARD_HEADING.match(lines[i].strip()):
            line = lines[i].strip()
            if line:
                body.append(line)
            i += 1

        # 标题也拼进 text：检索「虚短」时标题本身就是强语义信号
        text = f"{title}。" + " ".join(body)
        chunks.append(Chunk(text=text, source=f"公式卡片：{title}", heading=title))

    return chunks
