"""W4 评估报告脚本 —— 多次采样取多数，产出 5 个稳定指标。

用法（在 aurora 目录下）：
    uv run scripts/eval_report.py        # 默认每题采样 3 次
    uv run scripts/eval_report.py 5      # 每题采样 5 次

和 scripts/eval.py 的区别：
    eval.py 是「快速冒烟」——答案正确率用关键词命中（机械、会漏判）；
    eval_report.py 是「正式评测」——答案正确率换成 LLM-as-judge（语义判分），
    并补上幻觉率、引用命中率、延迟三个新指标。

为什么「多次采样」：pipeline 生成用 temperature=0.3，同一道题每次回答
措辞和推理都有波动（fb-4 三次跑结果是 对/错/错），单次评测有噪声。
所以每题跑 SAMPLES 次，离散指标（分类、判分、幻觉）取「多数投票」，
连续指标（引用命中率、延迟）取平均——这样数字才稳定、薄弱题才稳定暴露。

五个指标：
    1. 分类准确率  Router 判的 circuit_type 是否等于标注（多数投票）
    2. 答案正确率  LLM-as-judge 判 correct 的比例（多数投票，替代关键词命中）
    3. 幻觉率      回答是否编造了检索片段之外的内容（多数投票，仅 RAG 链路）
    4. 引用命中率  回答实际引用的来源数 / 检索返回的片段数（平均，仅 RAG 链路）
    5. 延迟        每题 pipeline 总耗时（平均）
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path

from aurora.embedder import encode_query
from aurora.judge import judge_answer, judge_hallucination
from aurora.pipeline import run
from aurora.vector_store import VectorStore

AURORA_ROOT = Path(__file__).resolve().parent.parent
EVAL_FILE = AURORA_ROOT / "data" / "eval_set.json"
INDEX_DIR = AURORA_ROOT / "data" / "index"

# 每题采样次数（可用命令行参数覆盖，如 `uv run scripts/eval_report.py 5`）
try:
    SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 3
except ValueError:
    SAMPLES = 3


def citation_hit_rate(answer: str, hits: list) -> float | None:
    """引用命中率 = 回答里实际引用的来源数 / 检索返回的片段数。

    RAG 回答会标注「（来源：xxx）」，所以「来源文件名是否出现在回答里」
    就能判断这个片段有没有被真正引用。无检索（hits 为空）时返回 None。
    """
    if not hits:
        return None
    cited = sum(1 for h in hits if h.chunk.source in (answer or ""))
    return cited / len(hits)


def sample_once(case: dict, store: VectorStore) -> dict:
    """跑一次完整评测，返回这次采样的各项结果（不打印，供多次采样用）。"""
    t0 = time.perf_counter()
    result = run(case["question"], store)
    elapsed = time.perf_counter() - t0

    classify_ok = result["circuit_type"] == case["circuit_type"]
    judge = judge_answer(case["answer"], result["answer"])

    hit_rate = None
    hallucinated = None
    if result["hits"]:
        hit_rate = citation_hit_rate(result["answer"], result["hits"])
        context = "\n".join(h.chunk.text for h in result["hits"])
        hallucinated = judge_hallucination(context, result["answer"])["hallucinated"]

    return {
        "classify_ok": classify_ok,
        "judgment": judge["judgment"],
        "hit_rate": hit_rate,
        "hallucinated": hallucinated,
        "elapsed": elapsed,
    }


def majority(values: list) -> object:
    """多数投票：返回出现次数最多的值（平票时取先出现的那一个）。"""
    return Counter(values).most_common(1)[0][0]


def _fmt_dist(dist: dict) -> str:
    """把判分分布格式化成「correct×2 wrong×1」这种字符串，用于展示稳定性。"""
    order = ["correct", "partial", "wrong"]
    return " ".join(f"{k}×{dist[k]}" for k in order if k in dist)


def main() -> int:
    cases = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    store = VectorStore.load(INDEX_DIR)
    print(f"已加载测试集 {len(cases)} 题，向量库 {len(store)} 块，每题采样 {SAMPLES} 次")
    print("=" * 70)

    # 预热 embedding 模型：bge 首次加载约需一分钟，会污染第一题的延迟指标。
    # 正式计时前先触发一次向量化，把冷启动排除在计时之外。
    print("预热 embedding 模型（首次加载较慢，请稍候）…")
    encode_query("预热：触发 bge 模型加载")

    rows = []
    for i, case in enumerate(cases, 1):
        samples = [sample_once(case, store) for _ in range(SAMPLES)]

        # 离散指标：多数投票
        classify_ok = majority([s["classify_ok"] for s in samples])
        judgment = majority([s["judgment"] for s in samples])
        hallus = [s["hallucinated"] for s in samples if s["hallucinated"] is not None]
        hallucinated = majority(hallus) if hallus else None

        # 连续指标：取平均
        hit_rates = [s["hit_rate"] for s in samples if s["hit_rate"] is not None]
        avg_hit = sum(hit_rates) / len(hit_rates) if hit_rates else None
        avg_elapsed = sum(s["elapsed"] for s in samples) / len(samples)

        # 稳定性：判分分布（fb-4 这种题会显示「correct×1 wrong×2」暴露波动）
        judgment_dist = dict(Counter(s["judgment"] for s in samples))
        classify_votes = sum(s["classify_ok"] for s in samples)
        unstable = len(judgment_dist) > 1

        rows.append({
            "id": case["id"],
            "classify_ok": classify_ok,
            "judgment": judgment,
            "hit_rate": avg_hit,
            "hallucinated": hallucinated,
            "elapsed": avg_elapsed,
        })

        flag = " ⚠不稳定" if unstable else ""
        print(f"\n[{i}/{len(cases)}] {case['id']}  分类{'✓' if classify_ok else '✗'}"
              f"({classify_votes}/{SAMPLES}) | 判分 {judgment:<8}（{_fmt_dist(judgment_dist)}）{flag}"
              f" | 延迟 {avg_elapsed:.2f}s")
        if avg_hit is not None:
            print(f"    引用命中率 {avg_hit:.0%} | 幻觉 {'有' if hallucinated else '无'}")

    # ---- 汇总（基于多数投票后的结果）----
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
    print(f"\n（每题采样 {SAMPLES} 次取多数，答案正确率由 LLM-as-judge 判分，裁判复用 DeepSeek）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
