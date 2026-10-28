"""课件 Markdown 切块（chunking）。

这是 RAG「离线建库」的第一环：把整篇课件切成一小段一小段。

为什么要切块，而不是整篇塞进向量库？
1. 向量是「整段文字一个坐标」。一篇课件几千字，主题混杂（电流源、虚短、
   稳压……），压成一个坐标会「糊成一团」，检索时永远命不中具体知识点。
2. 切成小段后，每段主题单一，向量坐标才「专一」，检索才准。
3. 检索出来是「一小段」而不是「一整篇」，拼进 prompt 不超 token。

对 PPT 抽取文本的特化处理：
markitdown 抽 PDF 会把公式符号、电路图框线变成「私有区乱码字符」（如 ``、
``），也会把排版抽成 markdown 表格（`| ... |`）。所以这里不是「含符号就丢
整行」，而是「清洗掉符号、留下文字，只在清洗后确实没字了才丢弃」——这样
能把「文字 + 公式混排」的行里那部分可读文字捞回来，而不是误杀。

切块的经验值：每块约 600 字（中文）。太小信息碎、太大主题散。
"""

import re
from dataclasses import dataclass
from pathlib import Path

# 段落累积到多少字就「封口」成一块
CHUNK_MAX = 600

# markitdown 抽取 PDF 时产生的乱码符号，主要落在 Unicode 私有区（PUA，0xE000
# 以上）。这些是公式符号、电路图框线，没有可读信息，清洗时直接丢掉。
_PRIVATE_USE_START = 0xE000

# 「汉字」正则 —— 中文课件的正文核心标志
_HAN = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class Chunk:
    """一个待向量化的文本片段，附来源信息（用于回答时引用出处、抑制幻觉）。"""

    text: str          # 片段正文
    source: str        # 来源文件名，如「6.3 集成运放及运算电路.md」
    heading: str = ""  # 最近一个标题，用于粗略定位「在哪一节」


def _clean_line(line: str) -> str:
    """清洗一行：去掉私有区乱码符号、表格竖线，压缩多余空白。"""
    cleaned = "".join(ch for ch in line if ord(ch) < _PRIVATE_USE_START)
    cleaned = cleaned.replace("|", " ")
    return " ".join(cleaned.split())


def _is_readable(cleaned: str) -> bool:
    """清洗后是否仍是「可读正文」：至少含一个汉字。

    用「含汉字」做标准，是因为课件正文几乎都是中文；而那些纯公式、纯电路图
    框线行（清洗后只剩 X、o、f、i 之类散字母），本就不适合做检索文本，
    交给后续「人工公式卡片」去补。
    """
    return bool(_HAN.search(cleaned))


# PPT 母版自带的占位提示文字，没有任何教学意义，抽出来后大量重复，直接丢弃。
_PLACEHOLDER_MARKS = ("请替换文字", "复制你的内容到此", "添加相关标题", "修改文字内容")


def _is_placeholder(cleaned: str) -> bool:
    """判断一行是不是 PPT 模板的占位提示文字。"""
    return any(mark in cleaned for mark in _PLACEHOLDER_MARKS)


def chunk_text(markdown_text: str, source: str = "") -> list[Chunk]:
    """把一段 Markdown 文本切成若干 Chunk。

    逻辑（朴素版，够用且好懂）：
    1. 逐行清洗（去乱码、去表格竖线）；
    2. 清洗后仍含汉字的行才保留；
    3. 把连续的可读行累积成一个「段落」，累积到 CHUNK_MAX 左右就封口。
    """
    chunks: list[Chunk] = []
    current: list[str] = []
    current_heading = ""

    def flush() -> None:
        """把当前累积的段落封成一个 Chunk（若不为空）。"""
        nonlocal current
        text = " ".join(current).strip()
        if text:
            chunks.append(Chunk(text=text, source=source, heading=current_heading))
        current = []

    for raw_line in markdown_text.splitlines():
        line = _clean_line(raw_line)
        if not _is_readable(line) or _is_placeholder(line):
            continue

        # 识别标题：markitdown 会把 PPT 标题抽成「1 集成运算放大器」这类数字
        # 开头、且较短的行。这里用一个宽松规则：短行 + 数字开头 = 视为标题。
        if len(line) <= 30 and line[0].isdigit():
            current_heading = line
            continue

        current.append(line)
        if len("".join(current)) >= CHUNK_MAX:
            flush()

    flush()  # 处理文件末尾残留的段落
    return chunks


def chunk_file(path: str | Path) -> list[Chunk]:
    """读取一个 md 文件并切块。source 用文件名（不含路径），便于展示出处。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return chunk_text(text, source=p.name)
