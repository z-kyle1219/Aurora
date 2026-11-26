"""教材 OCR 文本切块 —— 按章、节、语义单元切成可检索的 Chunk。

与 `chunker.py`（课件 PPT 转 Markdown 的朴素切块）的分工：
- `chunker.py` 面对 markitdown 抽出的 PPT 文本，标题靠「短行 + 数字开头」猜；
- 本模块面对扫描版教材的 OCR 文本，**章节结构来自目录清单**（data/textbook_toc.txt），
  页码可回溯到印刷页，因此不猜标题：用清单定位区域，再用行级指纹把区域切开。

三个必须保证的性质（对应入库验收要求）：
1. 出处可追溯：`source` 带教材名 + 印刷页码，`heading` 带章、节编号与标题；
2. 标题进 Embedding：块正文首句就是「章 + 节 + 标题」，命中片段本身能看出在哪一节；
3. 语义单元不拆散：例题（题设+解）、定义段、公式上下文尽量留在同一块，超长才切。

为什么复用 `chunker.Chunk`：`VectorStore.save/load` 依赖 `Chunk(text, source, heading)`
三字段的 dataclass 形状，加字段会让 `Chunk(**c)` 构造失败。故出处信息全部编码进
source / heading 两个字符串，磁盘格式保持不变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from aurora.chunker import Chunk

BOOK_TITLE = "电子技术基础·模拟部分（第六版）"
SOURCE_PREFIX = "教材《电子技术基础·模拟部分》第六版"

# PDF 页序（1-based）= 印刷页码 + PAGE_OFFSET（本书实测：印刷 P39 = PDF 第 61 页）
PAGE_OFFSET = 22

# 单块字符上限。bge-small-zh-v1.5 的 max_seq_length=512 token，中文近似 1 字 1 token，
# 留出标题与标点余量后取 420；建库脚本会用真实 tokenizer 实测截断率。
MAX_CHARS = 420

# 一行至少要有这么多汉字才算可读正文；否则视为公式残行/电路图框线残留
_MIN_HAN = 3

_HAN = re.compile(r"[\u4e00-\u9fff]")
_PAGE_NO = re.compile(r"^\s*\d{1,3}\s*$")
_PROBLEM_NO = re.compile(r"^\s*(\d{1,2}\.\d{1,2}\.\d{1,2})\s+(.*)$")
_EXAMPLE = re.compile(r"^\s*[\[【]?\s*例\s*(\d{1,2}\.\d{1,2}\.\d{1,2})")
_SENT_END = ("。", "！", "？", "；", "：")

# 习题筛选：题面依赖电路图的题（OCR 无法还原图），不入库，登记待校对
_FIGURE_HINT = ("如图", "图题", "图中", "见图", "图示")

# OCR 稳定讹误修正表（全书逐一统计过：只收「同一错法反复出现且改法唯一」的词）
# 之所以能安全替换：「信和号」在中文里不成立，出现 268 次全是「信号」被认错；
# 只做这一个高频词，避免大范围同义改写把原文改走样。其余零散错字进待校对清单。
_OCR_FIXES = {
    "信和号": "信号",
}

# 覆盖状态取值（覆盖清单与报告共用同一套词）
ST_OK = "已入库"
ST_THIN = "已入库·文本偏少"
ST_INTRO = "已入库·仅节引言"
ST_NO_HEAD = "已入库·标题未识别"
ST_NO_BODY = "已入库·无独立正文"
ST_EMPTY = "未入库·无正文"


@dataclass
class TocEntry:
    """目录清单里的一行：章 / 节 / 小节 / 小结 / 习题。"""

    level: int
    no: str
    title: str
    page: int          # 印刷页码


@dataclass
class Region:
    """一个待切块的连续区域（= 一个目录条目覆盖的正文）。"""

    level: int
    chapter_no: str
    chapter_title: str
    no: str
    title: str
    start_page: int
    end_page: int
    pairs: list[tuple[int, str]] = field(default_factory=list)
    heading_found: bool = False
    dropped_lines: int = 0
    drop_pages: dict[int, int] = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return sum(len(t) for _, t in self.pairs)

    @property
    def heading(self) -> str:
        """进入 Embedding 的章节标题（章 + 节 + 小节）。"""
        if self.chapter_no.isdigit():
            chapter = f"第{self.chapter_no}章 {self.chapter_title}"
        else:
            chapter = f"{self.chapter_no} {self.chapter_title}".strip()
        if self.no in ("小结", "习题"):
            return f"{chapter} {self.title}"
        if self.level == 1:
            return chapter
        return f"{chapter} {self.no} {self.title}"

    @property
    def source(self) -> str:
        pages = sorted({p for p, _ in self.pairs})
        if not pages:
            return f"{SOURCE_PREFIX} P{self.start_page}"
        if pages[0] == pages[-1]:
            return f"{SOURCE_PREFIX} P{pages[0]}"
        return f"{SOURCE_PREFIX} P{pages[0]}-{pages[-1]}"


@dataclass
class SectionRow:
    """覆盖清单的一行：每个小节都要有明确状态。"""

    chapter: str
    no: str
    title: str
    start_page: int
    end_page: int
    chars: int = 0
    chunks: int = 0
    dropped: int = 0
    status: str = ST_EMPTY
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "chapter": self.chapter, "no": self.no, "title": self.title,
            "start_page": self.start_page, "end_page": self.end_page,
            "chars": self.chars, "chunks": self.chunks, "dropped": self.dropped,
            "status": self.status, "note": self.note,
        }


@dataclass
class Problem:
    """一道习题：题号、起始印刷页、题面（OCR 清洗后）。"""

    no: str
    page: int
    text: str
    has_figure: bool
    answer: str = ""

    @property
    def eligible(self) -> bool:
        return not self.has_figure and len(_HAN.findall(self.text)) >= 40


# --------------------------------------------------------------------------- #
# 目录与分页文本
# --------------------------------------------------------------------------- #

def parse_toc(path: str | Path) -> list[TocEntry]:
    """解析目录清单（TAB 分隔：层级 / 编号 / 标题 / 印刷页码）。"""
    entries: list[TocEntry] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 4:
            continue
        entries.append(TocEntry(
            level=int(parts[0].strip()),
            no=parts[1].strip(),
            title=parts[2].strip(),
            page=int(parts[3].strip()),
        ))
    return entries


# 按印刷页码校正已核对的 OCR 片段，避免全书替换误伤正文。
_PAGE_OCR_FIXES = {
    61: (("比较硅、钞两种", "比较硅、锗两种"),),
    62: (("和猪二极管", "和锗二极管"),),
    64: (("0.2 V(销", "0.2 V(锗"),),
    158: (("硅管和钞管", "硅管和锗管"), ("硅(或销)", "硅(或锗)")),
    191: (("s.5 共集电极", "5.5 共集电极"), ("sS.$.1 共集电极", "5.5.1 共集电极")),
}


def load_pages(pages_dir: str | Path) -> dict[int, list[str]]:
    """读取 OCR 分页文本，返回 {印刷页码: 行列表}。

    文件名 pNNN.txt 是 PDF 页序（1-based），减去 PAGE_OFFSET 得到印刷页码；
    offset 之前的页（封面、版权、序、目录、符号表）不进入知识库。
    """
    pages: dict[int, list[str]] = {}
    for f in sorted(Path(pages_dir).glob("p*.txt")):
        m = re.search(r"p(\d+)\.txt$", f.name)
        if not m:
            continue
        printed = int(m.group(1)) - PAGE_OFFSET
        if printed < 1:
            continue
        page = f.read_text(encoding="utf-8")
        for original, corrected in _PAGE_OCR_FIXES.get(printed, ()):
            page = page.replace(original, corrected)
        pages[printed] = page.split("\n")
    return pages


# --------------------------------------------------------------------------- #
# 文本清洗
# --------------------------------------------------------------------------- #

def _norm(text: str) -> str:
    """归一化：去掉空白与常见标点，用于标题比对和重复行检测。"""
    drop = set("，。、；：？！“”‘’（）()《》〈〉【】·—….,;:?!\"'*-")
    return "".join(ch for ch in text if not ch.isspace() and ch not in drop)


def _find_noise_lines(pages: dict[int, list[str]], min_pages: int = 12) -> set[str]:
    """跨页统计高频短行 —— 页眉页脚（书眉章名、装饰符、页脚）就藏在这里。

    用数据驱动而非硬编码：OCR 出来的书眉形式不统一（有的带页码、有的带符号），
    但它们在全书里必然反复出现。
    """
    freq: dict[str, int] = {}
    for lines in pages.values():
        seen: set[str] = set()
        for raw in lines:
            line = " ".join(raw.split())
            if not line or len(line) > 24 or _PAGE_NO.match(line):
                continue
            key = _norm(line)
            if not key or key in seen:
                continue
            seen.add(key)
            freq[key] = freq.get(key, 0) + 1
    return {k for k, v in freq.items() if v >= min_pages}


def _looks_like_heading(line: str) -> bool:
    """结构行识别：编号标题 / 「小结」「习题」。

    必须单独识别，因为标题本身汉字很少（「1.1 信号」只有 2 个汉字），
    会被下面的「汉字数」阈值当成公式残行丢掉 —— 丢标题就等于丢了整节起点。
    """
    n = re.sub(r"\d+$", "", _norm(line))
    if n in ("小结", "本章小结", "习题", "思考题和习题"):
        return True
    if len(line) > 40:
        return False
    if not _HAN.search(line):
        return False
    return bool(re.match(r"^\s*\*?\s*(?:\d{1,2}(?:\.\d{1,2}){0,2}|[ABC]\.\d{1,2})\s+\S", line))


def _clean_page(lines: list[str], noise: set[str]) -> tuple[list[str], int]:
    """单页清洗：压缩空白、去页眉页脚、丢公式残行。返回（可读行，丢弃行数）。"""
    out: list[str] = []
    dropped = 0
    for raw in lines:
        line = " ".join(raw.split())
        if not line or _PAGE_NO.match(line):
            continue
        for wrong, right in _OCR_FIXES.items():
            if wrong in line:
                line = line.replace(wrong, right)
        if _norm(line) in noise:
            continue
        head, _, tail = line.partition(" ")
        if head.isdigit() and _norm(tail) in noise:      # 「40 2 运算放大器」式页脚
            continue
        if len(_HAN.findall(line)) < _MIN_HAN and not _looks_like_heading(line):
            dropped += 1
            continue
        out.append(line)
    return out, dropped


# --------------------------------------------------------------------------- #
# 区域定位
# --------------------------------------------------------------------------- #

def _loose(num: str) -> str:
    """把编号转成「允许字间有空格」的正则 —— OCR 常把「附录 A」「A. 1」拆开。"""
    return r"\s*".join(re.escape(ch) for ch in num)


def _heading_pattern(entry: TocEntry) -> re.Pattern[str] | None:
    if entry.no in ("小结", "习题"):
        return None
    num = entry.no.lstrip("*").strip()
    if re.fullmatch(r"\d{1,2}", num):                    # 章：OCR 常抽成「2 运算放大器」
        return re.compile(r"^\s*第?\s*" + _loose(num) + r"\s*章?\s+\S")
    return re.compile(r"^\s*\*?\s*" + _loose(num) + r"\s*(?=\S)")


def _is_special_heading(line: str, no: str) -> bool:
    """「小结/习题」类无编号标题。OCR 常把页码粘在同一行（「小 结 …… 83」），需剥尾数字。"""
    n = re.sub(r"\d+$", "", _norm(line))
    if no == "小结":
        return n in ("小结", "本章小结")
    if no == "习题":
        return n in ("习题", "思考题和习题")
    return False


def _find_heading(lines: list[str], entry: TocEntry, *, skip_top: int = 0) -> int | None:
    """在给定行里找该条目的标题行，返回行下标；找不到返回 None。

    `skip_top` 用来跳过页面顶部的书眉区：本书书眉常直接印「当前节的标题」，
    与正文里的真标题一字不差（P9 顶部是「1.5 放大电路的主要性能指标」书眉，
    真标题在同页第 46 行）。先搜书眉之外，找不到再回退到全文，才不会把上一节
    的尾巴算进本节。

    两级匹配：
    1. 编号严格匹配（`5.1.1 BJT 的结构简介`）；
    2. 编号被 OCR 认错时退化匹配 —— 扫描件里「5」常被认成 `s`/`S`（第 5 章整章
       开头就是 `s 双极结型三极管`），此时只要求「行首是小段非汉字残迹 + 标题」。
       为避免把正文里「如图 5.1.1 … 所示」当标题，还要求残迹里不含汉字。
    """
    pattern = _heading_pattern(entry)
    key = _norm(entry.title)
    head6 = key[:6]
    order = list(range(skip_top, len(lines))) + list(range(0, min(skip_top, len(lines))))
    for i in order:
        line = lines[i]
        if entry.no in ("小结", "习题"):
            if _is_special_heading(line, entry.no):
                return i
            continue
        if pattern is not None and pattern.match(line):
            return i
        if not head6:
            continue
        n = _norm(line)
        idx = n.find(head6)
        if idx < 0 or idx > 8 or len(n) > len(key) + 14:
            continue
        if not _HAN.search(n[:idx]):
            return i
    return None


def _entry_of(region: Region, page: int) -> TocEntry:
    return TocEntry(region.level, region.no, region.title, page)


def build_regions(entries: list[TocEntry], max_page: int) -> list[Region]:
    """把目录清单变成「有页范围」的区域列表（正文随后按页填充）。"""
    regions: list[Region] = []
    chapter_no, chapter_title = "", ""
    for idx, entry in enumerate(entries):
        if entry.level == 1:
            chapter_no, chapter_title = entry.no, entry.title
            if entry.no in ("参考文献", "索引"):
                continue                      # 非教学内容，不入库
        # 右边界取下一个条目的起始页；最后一个条目取全书末页（不是末页+1，
        # 否则会把不存在的印刷页码写进来源），否则会漏掉尾段
        next_page = entries[idx + 1].page if idx + 1 < len(entries) else max_page
        regions.append(Region(
            level=entry.level,
            chapter_no=chapter_no,
            chapter_title=chapter_title,
            no=entry.no,
            title=entry.title,
            start_page=entry.page,
            end_page=max(entry.page, next_page),
        ))
    return regions


def fill_region(region: Region, pages: dict[int, list[str]], next_region: Region | None,
                noise: set[str], seen: set[str], *, dedupe_min: int = 20) -> None:
    """按页范围取正文，在起止页用标题行裁掉不属于本区域的文字。

    起止页常常「上半页是上一节、下半页是下一节」，所以不能只靠页码，必须找标题行；
    找不到标题行时保守地保留整页（宁可多带上下文，也不要丢内容），并在报告里记
    「标题未识别」。
    """
    pairs: list[tuple[int, str]] = []
    for page in range(region.start_page, region.end_page + 1):
        raw = pages.get(page)
        if raw is None:
            continue
        cleaned, dropped = _clean_page(raw, noise)
        if dropped:
            region.drop_pages[page] = region.drop_pages.get(page, 0) + dropped
            region.dropped_lines += dropped

        if page == region.start_page:
            hit = _find_heading(cleaned, _entry_of(region, page), skip_top=2)
            if hit is not None:
                region.heading_found = True
                cleaned = cleaned[hit + 1:]
        if next_region is not None and page == next_region.start_page:
            hit = _find_heading(cleaned, _entry_of(next_region, page), skip_top=2)
            if hit is not None:
                cleaned = cleaned[:hit]
        pairs.extend((page, line) for line in cleaned)

    # 跨节重复正文去重：同一段文字（去标点后 ≥ dedupe_min 字）全书只保留首次出现
    kept: list[tuple[int, str]] = []
    for page, line in pairs:
        key = _norm(line)
        if len(key) >= dedupe_min:
            if key in seen:
                region.dropped_lines += 1
                continue
            seen.add(key)
        kept.append((page, line))
    region.pairs = kept


# --------------------------------------------------------------------------- #
# 切块
# --------------------------------------------------------------------------- #

def _is_subhead(line: str) -> bool:
    """节内的编号小标题，如「1. 输入电阻」「2. 输出电阻」。

    教材大量使用「1. 2. 3.」把一节拆成若干并列子话题（1.5 的性能指标、
    7.1.2 的几类电流源、9.3.2 的输出功率/管耗/效率）。不把它们当边界，
    一块里会并排四个子话题，向量被平均掉，检索时反而谁都命不中。
    """
    return len(line) <= 30 and bool(re.match(r"^\s*\d{1,2}\s*[.、]\s*\S", line))


def _units(pairs: list[tuple[int, str]], budget: int) -> list[list[tuple[int, str]]]:
    """把行聚成「语义单元」：遇到句末标点、编号小标题、或下一行是例题开头时断开。

    OCR 的行是排版行不是段落，直接按字数硬切会把「题设」和「解」分到两块，
    例题就废了。这里以句末标点和小标题为最小单元边界，兼顾可读性与块长。
    """
    units: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    size = 0
    for page, line in pairs:
        if cur and (_EXAMPLE.match(line) or _is_subhead(line) or size >= budget
                    or cur[-1][1].endswith(_SENT_END)):
            units.append(cur)
            cur, size = [], 0
        cur.append((page, line))
        size += len(line)
    if cur:
        units.append(cur)
    return units


def _split_long_unit(unit: list[tuple[int, str]], budget: int) -> list[list[tuple[int, str]]]:
    """单元本身超过预算时按句切；仍超长就按字符硬切（极少见，公式长句才会出现）。"""
    pieces: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    size = 0
    for page, line in unit:
        for sent in re.split(r"(?<=[。！？；])", line):
            sent = sent.strip()
            if not sent:
                continue
            while len(sent) > budget:            # 单句仍超长：硬切，避免超模型上限
                if cur:
                    pieces.append(cur)
                    cur, size = [], 0
                pieces.append([(page, sent[:budget])])
                sent = sent[budget:]
            if cur and size + len(sent) > budget:
                pieces.append(cur)
                cur, size = [], 0
            cur.append((page, sent))
            size += len(sent)
    if cur:
        pieces.append(cur)
    return pieces


def pack_region(region: Region, *, max_chars: int = MAX_CHARS) -> list[Chunk]:
    """把一个区域打包成若干 Chunk：每块都以章节标题开头，块内保留页码跨度。"""
    if not region.pairs:
        return []
    heading = region.heading
    budget = max(160, max_chars - len(heading) - 1)
    packed: list[tuple[str, list[int]]] = []
    buf: list[tuple[int, str]] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if not buf:
            return
        packed.append(("".join(t for _, t in buf), [p for p, _ in buf]))
        buf, size = [], 0

    for unit in _units(region.pairs, budget):
        if sum(len(t) for _, t in unit) > budget:
            for piece in _split_long_unit(unit, budget):
                if buf:
                    flush()
                buf, size = piece, sum(len(t) for _, t in piece)
                flush()
            continue
        unit_len = sum(len(t) for _, t in unit)
        # 例题从新块开始；编号小标题在当前块已有内容时也另起一块，
        # 避免「1. 输入电阻」「2. 输出电阻」两个子话题被并进同一向量
        if buf and (size + unit_len > budget or _EXAMPLE.match(unit[0][1])
                    or (_is_subhead(unit[0][1]) and size >= 150)):
            flush()
        buf.extend(unit)
        size += unit_len
    flush()

    # 尾巴过短（只有标题和半句话）没有检索价值，并回前一块，避免「空壳块」被命中
    if len(packed) >= 2 and len(packed[-1][0]) < 80:
        body, pages = packed.pop()
        prev_body, prev_pages = packed[-1]
        packed[-1] = (prev_body + body, prev_pages + pages)

    chunks: list[Chunk] = []
    for body, pages in packed:
        if len(body) < 40:                          # 只有标题 + 图注残留，没有检索价值
            continue
        chunks.append(Chunk(text=f"{heading}。{body}", source=_page_span(pages),
                            heading=heading))
    return chunks


def _page_span(pages: list[int]) -> str:
    lo, hi = min(pages), max(pages)
    return f"{SOURCE_PREFIX} P{lo}-{hi}" if lo != hi else f"{SOURCE_PREFIX} P{lo}"


# --------------------------------------------------------------------------- #
# 习题：只留代表题，题面完整的才入库
# --------------------------------------------------------------------------- #

def extract_problems(region: Region) -> list[Problem]:
    """把「习题」区域拆成单题。题面依赖电路图的题会被标记，不参与入库。"""
    problems: list[Problem] = []
    cur_no, cur_page = "", 0
    cur: list[str] = []
    for page, line in region.pairs:
        m = _PROBLEM_NO.match(line)
        if m:
            if cur_no:
                problems.append(_make_problem(cur_no, cur_page, cur))
            cur_no, cur_page, cur = m.group(1), page, [line]
        elif cur_no:
            cur.append(line)
    if cur_no:
        problems.append(_make_problem(cur_no, cur_page, cur))
    return problems


def _make_problem(no: str, page: int, lines: list[str]) -> Problem:
    text = "".join(lines)
    return Problem(no=no, page=page, text=text,
                   has_figure=any(h in text for h in _FIGURE_HINT))


def parse_answers(region: Region) -> dict[str, str]:
    """从「部分习题答案」拆出 {题号: 答案文本}，供入库时标注「有无解答」。"""
    answers: dict[str, str] = {}
    cur_no = ""
    cur: list[str] = []
    for _, line in region.pairs:
        m = _PROBLEM_NO.match(line)
        if m:
            if cur_no:
                answers[cur_no] = "".join(cur)[:300]
            cur_no, cur = m.group(1), [line]
        elif cur_no:
            cur.append(line)
    if cur_no:
        answers[cur_no] = "".join(cur)[:300]
    return answers


def _tags(text: str) -> set[str]:
    tags: set[str] = set()
    for key, tag in (("求", "计算"), ("分析", "分析"), ("说明", "说明"),
                     ("判断", "判断"), ("设计", "设计"), ("证明", "证明"),
                     ("为什么", "说理"), ("画出", "作图")):
        if key in text:
            tags.add(tag)
    return tags


def select_problems(problems: list[Problem], k: int) -> tuple[list[Problem], list[Problem]]:
    """挑代表题：先按「题型标签」铺开覆盖，不够再按题号顺序补。返回（入选，落选且有图）。"""
    eligible = [p for p in problems if p.eligible]
    skipped = [p for p in problems if not p.eligible]
    picked: list[Problem] = []
    covered: set[str] = set()
    for p in eligible:                       # 第一轮：覆盖不同题型
        tags = _tags(p.text)
        if tags - covered:
            picked.append(p)
            covered |= tags
        if len(picked) >= k:
            break
    for p in eligible:                       # 第二轮：按题号补齐
        if len(picked) >= k:
            break
        if p not in picked:
            picked.append(p)
    return picked[:k], skipped


def problem_chunks(region: Region, problems: list[Problem], max_chars: int) -> list[Chunk]:
    """把入选习题变成 Chunk：题面 + 解答状态（教材是否给出答案）。"""
    heading = f"{region.heading}"
    chunks: list[Chunk] = []
    budget = max_chars - len(heading) - 1
    for p in problems:
        tail = f"【教材答案】{p.answer}" if p.answer else "【解答状态】教材未给出本题解答（仅部分习题附答案）"
        body = f"习题 {p.no}（P{p.page}）{p.text}{tail}"
        if len(body) > budget:
            body = body[:budget]
        chunks.append(Chunk(
            text=f"{heading} {p.no}。{body}",
            source=f"{SOURCE_PREFIX} P{p.page}",
            heading=f"{heading} {p.no}",
        ))
    return chunks


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #

def build_textbook_chunks(
    toc_path: str | Path,
    pages_dir: str | Path,
    *,
    max_chars: int = MAX_CHARS,
    problems_per_chapter: int = 3,
) -> tuple[list[Chunk], list[SectionRow], dict]:
    """读目录 + OCR 分页文本，产出 (chunks, 覆盖行, 统计信息)。"""
    entries = parse_toc(toc_path)
    pages = load_pages(pages_dir)
    if not pages:
        raise FileNotFoundError(f"没有找到 OCR 分页文本：{pages_dir}")

    noise = _find_noise_lines(pages)
    regions = build_regions(entries, max(pages))

    answers: dict[str, str] = {}
    # 「部分习题答案」在清单里编号为 附录D，先填它才能给入选习题标「有无解答」
    answer_region = next((r for r in regions if r.no == "附录D"), None)

    seen: set[str] = set()
    for idx, region in enumerate(regions):
        nxt = regions[idx + 1] if idx + 1 < len(regions) else None
        fill_region(region, pages, nxt, noise, seen)
    if answer_region is not None:
        answers = parse_answers(answer_region)

    chunks: list[Chunk] = []
    rows: list[SectionRow] = []
    # 有下级小节的节：它们的正文分散在小节里，本身只剩一段引言，
    # 不能当作「文本偏少」的覆盖问题来看。
    has_child = {
        id(r) for i, r in enumerate(regions[:-1])
        if r.level == 2 and regions[i + 1].level == 3
        and regions[i + 1].start_page <= r.end_page
    }
    stats = {
        "pages_used": len(pages),
        "regions": len(regions),
        "noise_lines": len(noise),
        "problems_selected": 0,
        "problems_skipped_figure": 0,
        "problems_with_answer": 0,
    }

    for region in regions:
        if region.no == "附录D":
            # 答案区只作为「有无解答」的来源，不单独入库（脱离题干的答案没有检索价值）
            rows.append(SectionRow(
                chapter=region.chapter_no, no=region.no, title=region.title,
                start_page=region.start_page, end_page=region.end_page,
                chars=region.chars, chunks=0, dropped=region.dropped_lines,
                status="仅作答案来源", note="按题号挂到入选习题上，不单独入库",
            ))
            continue

        if region.no == "习题":
            problems = extract_problems(region)
            picked, skipped = select_problems(problems, problems_per_chapter)
            for p in picked:
                p.answer = answers.get(p.no, "")
            region_chunks = problem_chunks(region, picked, max_chars)
            stats["problems_selected"] += len(picked)
            stats["problems_skipped_figure"] += len(skipped)
            stats["problems_with_answer"] += sum(1 for p in picked if p.answer)
            note = (f"共 {len(problems)} 题；入选 {len(picked)} 题"
                    f"（{', '.join(p.no for p in picked)}）；"
                    f"{len(skipped)} 题题面依赖电路图或过短，"
                    f"{len(problems) - len(picked) - len(skipped)} 题因同类代表已覆盖未入选")
            if not region_chunks:
                note += "；本章无可用代表题，习题未入库"
                status = ST_EMPTY
            else:
                status = ST_OK
            chunks.extend(region_chunks)
            rows.append(SectionRow(
                chapter=region.chapter_no, no=region.no, title=region.title,
                start_page=region.start_page, end_page=region.end_page,
                chars=region.chars, chunks=len(region_chunks),
                dropped=region.dropped_lines, status=status, note=note,
            ))
            continue

        region_chunks = pack_region(region, max_chars=max_chars)
        chunks.extend(region_chunks)
        status = ST_OK
        note = ""
        if not region_chunks:
            if region.heading_found and region.chars == 0:
                status = ST_NO_BODY
                note = "标题后直接进入下一节，本节无独立正文（内容在小节里）"
            elif region.chars > 0:
                status = ST_THIN
                note = f"正文仅 {region.chars} 字且以图/公式为主，未生成可用块"
            else:
                status = ST_EMPTY
                note = "该页范围内没有识别出可用正文"
        elif region.chars < 150:
            if id(region) in has_child:
                status = ST_INTRO
                note = f"本节仅 {region.chars} 字节引言，正文在下列小节"
            else:
                status = ST_THIN
                note = f"仅 {region.chars} 字，可能整节以图为主"
        elif not region.heading_found and region.level > 1:
            status = ST_NO_HEAD
            note = "起始页未定位到标题行，按页码范围取文（可能带入上节尾部）"
        if region.drop_pages:
            worst = max(region.drop_pages.items(), key=lambda kv: kv[1])
            note = (note + "；" if note else "") + \
                   f"丢弃无正文行 {region.dropped_lines} 行，最多在 P{worst[0]}（{worst[1]} 行）"
        rows.append(SectionRow(
            chapter=region.chapter_no, no=region.no, title=region.title,
            start_page=region.start_page, end_page=region.end_page,
            chars=region.chars, chunks=len(region_chunks),
            dropped=region.dropped_lines, status=status, note=note,
        ))

    return chunks, rows, stats
