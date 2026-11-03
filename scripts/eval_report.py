"""W4 评估报告脚本 —— 跑完整评测，产出 5 个指标。

用法（在 aurora 目录下）：
    uv run scripts/eval_report.py

和 scripts/eval.py 的区别：
    eval.py 是「快速冒烟」——答案正确率用关键词命中（机械、会漏判）；
    eval_report.py 是「正式评测」——答案正确率换成 LLM-as-judge（语义判分），
    并补上幻觉率、引用命中率、延迟三个新指标。

五个指标：
    1. 分类准确率  Router 判的 circuit_type 是否等于标注（精确字符串对比）
    2. 答案正确率  LLM-as-judge 判 correct 的比例（替代关键词命中）
    3. 幻觉率      回答是否编造了检索片段之外的内容（仅 RAG 链路）
    4. 引用命中率  回答实际引用的来源数 / 检索返回的片段数（仅 RAG 链路）
    5. 延迟        每题 pipeline 总耗时，汇总平均
"""

import json
import sys
import time
from pathlib import Path

from aurora.judge import judge_answer, judge_hallucination
from aurora.pipeline import run
from aurora.router import TYPE_LABELS
from aurora.vector_store import VectorStore

AURORA_ROOT = Path(__file__).resolve().parent.parent
EVAL_FILE = AURORA_ROOT / "data" / "eval_set.json"
INDEX_DIR = AURORA_ROOT / "data" / "index"


def citation_hit_rate(answer: str, hits: list) -> float | None:
    """引用命中率 = 回答里实际引用的来源数 / 检索返回的片段数。

    RAG 回答会标注「（来源：xxx）」，所以「来源文件名是否出现在回答里」
    就能判断这个片段有没有被真正引用。无检索（hits 为空）时返回 None。
    """
    if not hits:
        return None
    cited = sum(1 for h in hits if h.chunk.source in (answer or ""))
    return cited / len(hits)


def main() -> int:
    cases = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    store = VectorStore.load(INDEX_DIR)
    print(f"已加载测试集 {len(cases)} 题，向量库 {len(store)} 块")
    print("=" * 70)

    rows = []
    for i, case in enumerate(cases, 1):
        # 延迟：计时整条 pipeline
        t0 = time.perf_counter()
        result = run(case["question"], store)
        elapsed = time.perf_counter() - t0

        classify_ok = result["circuit_type"] == case["circuit_type"]

        # 答案正确率：LLM-as-judge
        judge = judge_answer(case["answer"], result["answer"])
        answer_ok = judge["judgment"] == "correct"

        # 引用命中率 + 幻觉率：仅 RAG 链路（有检索片段的题）
        hit_rate = None
        halluc = None
        if result["hits"]:
            hit_rate = citation_hit_rate(result["answer"], result["hits"])
            context = "\n".join(h.chunk.text for h in result["hits"])
            halluc = judge_hallucination(context, result["answer"])

        rows.append({
            "id": case["id"],
            "classify_ok": classify_ok,
            "judgment": judge["judgment"],
            "hit_rate": hit_rate,
            "hallucinated": halluc["hallucinated"] if halluc else None,
            "elapsed": elapsed,
        })

        # 逐题明细
        print(f"\n[{i}/{len(cases)}] {case['id']}  分类{'✓' if classify_ok else '✗'} "
              f"| 判分 {judge['judgment']:<8} | 延迟 {elapsed:.2f}s")
        if hit_rate is not None:
            print(f"    引用命中率 {hit_rate:.0%} | 幻觉 {'有' if halluc['hallucinated'] else '无'}")

    # ---- 汇总 ----
    total = len(cases)
    n_classify = sum(r["classify_ok"] for r in rows)
    n_correct = sum(r["judgment"] == "correct" for r in rows)
    n_partial = sum(r["judgment"] == "partial" for r in rows)
    n_wrong = sum(r["judgment"] == "wrong" for r in rows)

    rag_rows = [r for r in rows if r["hit_rate"] is not None]
    n_rag = len(rag_rows)
    avg_hit = sum(r["hit_rate"] for r in rag_rows) / n_rag if n_rag else 0
    n_halluc = sum(r["hallucinated"] for r in rag_rows)
    avg_latency = sum(r["elapsed"] for r in rows) / total

    print("\n" + "=" * 70)
    print("评估报告汇总")
    print("=" * 70)
    print(f"1. 分类准确率 : {n_classify}/{total} = {n_classify / total:.1%}")
    print(f"2. 答案正确率 : {n_correct}/{total} = {n_correct / total:.1%}"
          f"（correct {n_correct} / partial {n_partial} / wrong {n_wrong}）")
    print(f"3. 幻觉率     : {n_halluc}/{n_rag} = {n_halluc / n_rag:.1%}" if n_rag else "3. 幻觉率     : 无 RAG 题")
    print(f"4. 引用命中率 : 平均 {avg_hit:.1%}（仅 {n_rag} 道 RAG 题）")
    print(f"5. 平均延迟   : {avg_latency:.2f}s / 题")
    print("\n（答案正确率由 LLM-as-judge 判分，裁判复用 DeepSeek）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
