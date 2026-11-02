"""W3 评测脚本 —— 跑第一版正确率（分类准确率 + 答案正确率）。

用法（在 aurora 目录下）：
    uv run scripts/eval.py

遍历 data/eval_set.json 的测试集，逐题走完整流水线（Router 分流 → 分派 → 生成），
统计两个指标：
    1. 分类准确率：Router 判出的 circuit_type 是否等于标注值（精确字符串对比）；
    2. 答案正确率：期望关键词是否出现在最终回答里（启发式，W4 换 LLM-as-judge）。

这是「可评测解题流水线」的第一次落地：此前只验证了「能跑通」，这里开始量化
「跑得对不对」。跑之前先 build_index.py 建库。
"""

import json
import sys
from pathlib import Path

from aurora.pipeline import run
from aurora.router import TYPE_LABELS
from aurora.vector_store import VectorStore

AURORA_ROOT = Path(__file__).resolve().parent.parent
EVAL_FILE = AURORA_ROOT / "data" / "eval_set.json"
INDEX_DIR = AURORA_ROOT / "data" / "index"


def is_correct(model_answer: str, expected: str) -> bool:
    """启发式判分：期望关键词（用 | 分隔）任一命中即算对。"""
    for kw in expected.split("|"):
        if kw in (model_answer or ""):
            return True
    return False


def main() -> int:
    cases = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    store = VectorStore.load(INDEX_DIR)
    print(f"已加载测试集 {len(cases)} 题，向量库 {len(store)} 块")
    print("=" * 70)

    n_classify_ok = 0
    n_answer_ok = 0

    for i, case in enumerate(cases, 1):
        result = run(case["question"], store)

        pred_type = result["circuit_type"]
        true_type = case["circuit_type"]
        classify_ok = pred_type == true_type
        answer_ok = is_correct(result["answer"], case["answer"])
        n_classify_ok += classify_ok
        n_answer_ok += answer_ok

        mark_c = "✓" if classify_ok else "✗"
        mark_a = "✓" if answer_ok else "✗"
        print(f"\n[{i}/{len(cases)}] {case['id']}  {case['question'][:42]}…")
        print(f"  分类 {mark_c}  预测「{TYPE_LABELS[pred_type]}」| 标注「{TYPE_LABELS[true_type]}」"
              f"（置信度 {result['confidence']:.2f}）")
        print(f"  答案 {mark_a}  期望关键词「{case['answer']}」")
        print(f"  回答: {result['answer'][:100]}")

    total = len(cases)
    print("\n" + "=" * 70)
    print(f"分类准确率: {n_classify_ok}/{total} = {n_classify_ok / total:.1%}")
    print(f"答案正确率: {n_answer_ok}/{total} = {n_answer_ok / total:.1%}")
    print("（答案正确率是关键词命中启发式，W4 换 LLM-as-judge 精确判分）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
