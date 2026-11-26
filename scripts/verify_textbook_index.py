"""教材索检索验收 —— 逐章跑代表性问题，只用本地 Embedding + 向量检索。

不调用任何聊天 API：只用 `rag.retrieve()`（encode_query + VectorStore.search），
因此可以在离线环境复现，也保证「检索质量」和「生成质量」分开评估。

判定口径（刻意分开，避免自欺）：
- `命中`：top-3 里出现预期小节 —— 只说明「提到了关键词/位置对」；
- `可直答`：需要人工看片段能否支撑答案，脚本不代判，留给验收报告逐条写。

用法（在 aurora 目录下）：
    uv run scripts/verify_textbook_index.py                       # 默认 data/index-textbook
    uv run scripts/verify_textbook_index.py --index data/index    # 对比默认索引
    uv run scripts/verify_textbook_index.py --md out.md           # 额外落一份 markdown 表
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AURORA_ROOT = Path(__file__).resolve().parent.parent

from aurora.rag import retrieve
from aurora.vector_store import VectorStore

# 每章至少 2 题，覆盖「核心概念」与「该章主要分析/应用」两类。
# 带 ★ 的是本次任务要求必须回归的四道题。
QUESTIONS: list[dict] = [
    {"ch": "第1章", "q": "放大电路的输入电阻和输出电阻对信号源和负载有什么影响", "expect": ["1.5"]},
    {"ch": "第1章", "q": "放大电路有哪几种模型，受控源怎么表示", "expect": ["1.4"]},

    {"ch": "第2章", "q": "理想运放的虚短和虚断是怎么来的", "expect": ["2.2"]},
    {"ch": "第2章", "q": "同相放大电路的电压增益怎么算", "expect": ["2.3.1"]},
    {"ch": "第2章", "q": "反相比例运算电路为什么反相输入端是虚地", "expect": ["2.3.2"]},
    {"ch": "第2章", "q": "积分电路输出电压与输入电压的关系式", "expect": ["2.4.4"]},

    {"ch": "第3章", "q": "二极管是什么", "expect": ["3.3"], "must": True},
    {"ch": "第3章", "q": "二极管怎么分类", "expect": ["3.3", "3.5"], "must": True},
    {"ch": "第3章", "q": "PN 结正向偏置会怎样", "expect": ["3.2.3"], "must": True},
    {"ch": "第3章", "q": "PN 结的形成过程和空间电荷区", "expect": ["3.2.2"]},
    {"ch": "第3章", "q": "齐纳二极管工作在什么状态，有什么用途", "expect": ["3.5.1"]},
    {"ch": "第3章", "q": "二极管的主要参数有哪些", "expect": ["3.3.3"]},

    {"ch": "第4章", "q": "N 沟道增强型 MOSFET 的工作原理和开启电压", "expect": ["4.1.1"]},
    {"ch": "第4章", "q": "共源极放大电路用小信号模型法求电压增益", "expect": ["4.4.2", "4.4"]},
    {"ch": "第4章", "q": "MOSFET 放大电路三种组态怎么区分", "expect": ["4.5.3", "4.5"]},

    {"ch": "第5章", "q": "BJT 工作在放大区时集电极电流和基极电流的关系", "expect": ["5.1.2"]},
    {"ch": "第5章", "q": "射极偏置电路怎么稳定静态工作点", "expect": ["5.4.2"]},
    {"ch": "第5章", "q": "共集电极放大电路的特点和用途", "expect": ["5.5.1"]},

    {"ch": "第6章", "q": "RC 低通电路的截止频率怎么求", "expect": ["6.2.2"]},
    {"ch": "第6章", "q": "共射放大电路高频时增益为什么下降", "expect": ["6.4.4", "6.4"]},

    {"ch": "第7章", "q": "镜像电流源的工作原理", "expect": ["7.1.2", "7.1"]},
    {"ch": "第7章", "q": "差分放大电路为什么能抑制共模信号", "expect": ["7.2.1", "7.2"]},

    {"ch": "第8章", "q": "负反馈放大电路四种组态怎么判断", "expect": ["8.1.6"]},
    {"ch": "第8章", "q": "深度负反馈条件下闭环增益怎么近似计算", "expect": ["8.4"]},
    {"ch": "第8章", "q": "负反馈对输入电阻和输出电阻有什么影响", "expect": ["8.3.4"]},

    {"ch": "第9章", "q": "乙类互补对称功率放大电路的最大输出功率", "expect": ["9.3.2"]},
    {"ch": "第9章", "q": "交越失真是怎么产生的，怎么消除", "expect": ["9.4"]},

    {"ch": "第10章", "q": "有源低通滤波电路的组成和传递函数", "expect": ["10.3.1", "10.2"]},
    {"ch": "第10章", "q": "RC 桥式正弦波振荡电路的振荡频率", "expect": ["10.6"]},

    {"ch": "第11章", "q": "整流滤波时二极管什么时候导通", "expect": ["11.1.2", "11.1.1"],
     "must": True},
    {"ch": "第11章", "q": "串联反馈式稳压电路的工作原理", "expect": ["11.2.2"]},
    {"ch": "第11章", "q": "单相桥式整流电路输出直流电压平均值", "expect": ["11.1.1"]},

    {"ch": "第12章", "q": "用 SPICE 分析电子电路的一般步骤", "expect": ["12.1"]},
    {"ch": "第12章", "q": "SPICE 模型比小信号模型精度更高的原因", "expect": ["12.1"]},
]


def _short(hit) -> str:
    return f"{hit.chunk.heading}｜{hit.chunk.source.split('第六版')[-1].strip()}｜{hit.score:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=AURORA_ROOT / "data" / "index-textbook")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--md", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    store = VectorStore.load(args.index)
    print(f"索引 {args.index}：{len(store)} 块，向量 {store.vectors.shape}\n")

    rows: list[dict] = []
    ok = 0
    for item in QUESTIONS:
        hits = retrieve(store, item["q"], top_k=args.top_k)
        joined = " ".join(h.chunk.heading for h in hits)
        hit_flag = any(e in joined for e in item["expect"])
        ok += hit_flag
        rows.append({
            "chapter": item["ch"],
            "question": item["q"],
            "expect": item["expect"],
            "must": item.get("must", False),
            "hit": hit_flag,
            "hits": [
                {"heading": h.chunk.heading, "source": h.chunk.source, "score": round(h.score, 4),
                 "preview": h.chunk.text[len(h.chunk.heading) + 1:][:120]}
                for h in hits
            ],
        })
        mark = "★" if item.get("must") else " "
        print(f"{mark}{item['ch']} {item['q']}")
        for h in hits:
            print(f"    {h.score:.3f}  {_short(h)}")
        for e in item["expect"]:
            print(f"    预期 {e} -> {'命中' if e in joined else '未命中'}")
        print()

    print(f"命中率（top-{args.top_k} 出现预期小节）：{ok}/{len(QUESTIONS)}"
          f" = {ok / len(QUESTIONS):.1%}")

    if args.md:
        lines = ["| 章 | 问题 | 预期小节 | top-3 命中出处（相似度） | 判定 |", "|---|---|---|---|---|"]
        for r in rows:
            hits = "<br>".join(f"{h['heading']}（{h['source'].split('第六版')[-1].strip()}，{h['score']:.3f}）"
                               for h in r["hits"])
            lines.append(f"| {r['chapter']} | {r['question']} | {', '.join(r['expect'])} | {hits} | "
                         f"{'命中' if r['hit'] else '未命中'} |")
        args.md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"markdown 表：{args.md}")

    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细：{args.json}")

    return 0 if ok == len(QUESTIONS) else 2


if __name__ == "__main__":
    sys.exit(main())
