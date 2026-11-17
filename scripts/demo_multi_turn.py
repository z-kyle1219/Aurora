"""W4 多轮会话演示 —— 同一个 Session 连跑三轮，看历史、改写、分类怎么走。

用法（在 aurora 目录下）：
    uv run scripts/demo_multi_turn.py

三个观测点：

1. **历史不漏**：第一轮 0 条，第二轮 2 条，第三轮 4 条；
2. **改写在干活**：第二轮的「那如果 R1 换成 20kΩ 呢」会被补成一句完整的问句，
   Router 和检索拿到的都是它；
3. **换话题不被拽回去**（第三轮的意义）：学生说「不聊这个了，说说负反馈」，
   因为 Router 读的是改写后的独立问题、不是整段历史，类型应该干净地切到
   feedback —— 这也是「一次改写、两处消费」要验证的核心收益。

为什么第二问故意写得这么省略 —— 这句话里没有「运放」「增益」「反相」任何
字眼，是典型的多轮追问形态。改造前它会让 Router 靠猜、让检索扑空；
改造后靠改写层还原。
"""

from pathlib import Path

from aurora.pipeline import run
from aurora.router import TYPE_LABELS
from aurora.session import Session
from aurora.vector_store import VectorStore

AURORA_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = AURORA_ROOT / "data" / "index"

# 第一问给全参数，第二问只改一个电阻，第三问主动换话题
TURNS = [
    "反相比例运算电路：R1=10kΩ，Rf=100kΩ，ui=-3V，电源 ±15V，求 uo。",
    "那如果 R1 换成 20kΩ 呢？",
    "不聊这个了，说说负反馈四种组态怎么判断？",
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
        print(f"  → 改写为：“{result['standalone']}”")
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

    # 断言写回链路：三轮 = 六条消息（u1, a1, u2, a2, u3, a3）
    print(f"\n[会话结束] 共 {len(session)} 条消息 = {len(session) // 2} 轮问答")
    assert len(session) == 6, f"预期 6 条，实际 {len(session)} 条 —— 写回链路有问题"
    print("✓ 闭合验证通过：第一轮 2 条，第二轮 4 条，第三轮 6 条")


if __name__ == "__main__":
    main()
