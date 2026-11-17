"""W4 多轮会话演示 —— 同一个 Session 连跑两轮，看历史怎么累积。

用法（在 aurora 目录下）：
    uv run scripts/demo_multi_turn.py

这个脚本验证一件事：**管道不漏**。两个观测点：

1. 每轮开头的「交给模型的历史」条数：第一轮 0、第二轮 2；
2. 每轮结束的 history 条数：第一轮 2、第二轮 4。

为什么第二问故意写成「那如果 R1 换成 20kΩ 呢」—— 这句话里没有
「运放」「增益」「反相」任何字眼，是典型的多轮追问形态。它同时暴露了
两个已知缺口：
    - Router 只吃这一句，可能判成 concept 而不是 opamp；
    - 检索（走 RAG 时）也是拿这一句做 query，指代没有还原。
所以本脚本只断言「历史写回」这件事（2 → 4），不断言 Router 判得对 ——
判错也是真实结果，正好可以观察。
"""

from pathlib import Path

from aurora.pipeline import run
from aurora.router import TYPE_LABELS
from aurora.session import Session
from aurora.vector_store import VectorStore

AURORA_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = AURORA_ROOT / "data" / "index"

# 第一问给全参数，第二问只改一个电阻 —— 学生最自然的多轮追问
TURNS = [
    "反相比例运算电路：R1=10kΩ，Rf=100kΩ，ui=-3V，电源 ±15V，求 uo。",
    "那如果 R1 换成 20kΩ 呢？",
]


def main() -> None:
    store = VectorStore.load(INDEX_DIR)
    session = Session()

    print(f"W4 多轮会话演示（向量库 {len(store)} 块）")
    print("=" * 70)

    for i, question in enumerate(TURNS, 1):
        before = len(session)  # 这一轮开始前，历史累积到几条

        result = run(question, store, session=session)
        label = TYPE_LABELS[result["circuit_type"]]

        print(f"\n[第 {i} 轮提问] {question}")
        print(f"  交给模型的历史：{before} 条")
        print(f"  → Router 判为「{label}」（置信度 {result['confidence']:.2f}）")
        print(f"  → 本轮结束后 history：{len(session)} 条")

        if result["trace"]:
            print("  [工具调用过程]")
            for step in result["trace"]:
                print(f"    - 调用 {step['tool']}({step['arguments']}) → {step['result']}")
                if step["warning"]:
                    print(f"      [!] 校验警告: {step['warning']}")
        elif result["hits"] is not None:
            print("  [检索到的片段]")
            for j, h in enumerate(result["hits"], 1):
                print(f"    {j}. 相似度 {h.score:.3f} | 来源 {h.chunk.source}")

        print(f"\n  [Aurora 回答]\n{result['answer']}")
        print("-" * 70)

    # 只断言写回链路：两轮 = 四条消息（u1, a1, u2, a2）
    print(f"\n[会话结束] 共 {len(session)} 条消息 = {len(session) // 2} 轮问答")
    assert len(session) == 4, f"预期 4 条，实际 {len(session)} 条 —— 写回链路有问题"
    print("✓ 闭合验证通过：第一轮 2 条，第二轮 4 条")


if __name__ == "__main__":
    main()
