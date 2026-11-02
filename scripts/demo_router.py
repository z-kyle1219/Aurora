"""W3 Router 演示 —— 五类电路分流一次看全。

用法（在 aurora 目录下）：
    uv run scripts/demo_router.py

每类电路一题，展示：Router 判了什么类型 → 走了哪条链路 → 最终回答。
"""

from pathlib import Path

from aurora.pipeline import run
from aurora.router import TYPE_LABELS
from aurora.vector_store import VectorStore

AURORA_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = AURORA_ROOT / "data" / "index"

# 每类一题（正好覆盖五条链路）
QUESTIONS = [
    "反相比例运算电路：R1=10kΩ，Rf=100kΩ，ui=-0.5V，电源 ±15V，求 uo。",
    "要稳定输出电压且增大输入电阻，应选哪种负反馈组态？",
    "直流稳压电源由哪几个部分组成？",
    "什么是虚短和虚断？",
    "请帮我分析这个 JK 触发器的时序逻辑。",
]


def main() -> None:
    store = VectorStore.load(INDEX_DIR)
    print(f"W3 Router 演示：五类电路分流（向量库 {len(store)} 块）")
    print("=" * 70)

    for q in QUESTIONS:
        result = run(q, store)
        label = TYPE_LABELS[result["circuit_type"]]

        print(f"\n[学生问题] {q}")
        print(f"  → Router 判为「{label}」（置信度 {result['confidence']:.2f}）")
        print(f"    理由: {result['reason']}")

        if result["trace"]:
            print("  [工具调用过程]")
            for step in result["trace"]:
                print(f"    - 调用 {step['tool']}({step['arguments']}) → {step['result']}")
                if step["warning"]:
                    print(f"      [!] 校验警告: {step['warning']}")
        elif result["hits"] is not None:
            print("  [检索到的片段]")
            for i, h in enumerate(result["hits"], 1):
                print(f"    {i}. 相似度 {h.score:.3f} | 来源 {h.chunk.source}")

        print(f"\n  [Aurora 回答]\n{result['answer']}")
        print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
