"""交互式多轮对话：亲手体验 history 是怎么累积的。

用法（在 aurora 目录下）：
    uv run scripts/chat_loop.py

输入 exit / quit / 退出 结束对话。

这个脚本想让你「看到」上一课讲的东西：
1. 模型是无状态的，所以每一轮都要把 history 完整发回去；
2. history 会一轮一轮变长——看每轮结束后打印的「已累积 N 条消息」；
3. 所谓「多轮对话」，本质就是你我在代码里手动维护这个 history 列表。
"""

from aurora.companion_v0 import ask_stream


def main() -> None:
    print("=== Aurora 电路学伴（多轮对话）===")
    print("输入 exit 退出。开始提问吧：\n")

    # 这就是「对话记忆」：一个列表，每一轮都会往里追加两条消息
    history: list[dict] = []

    while True:
        try:
            question = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if question.lower() in {"exit", "quit", "退出"}:
            print("再见！")
            break
        if not question:
            continue

        # 流式打印回答
        print("Aurora：", end="", flush=True)
        pieces = []
        for piece in ask_stream(question, history=history):
            print(piece, end="", flush=True)
            pieces.append(piece)
        answer = "".join(pieces)
        print()

        # 关键：把这一轮「你问我答」追加进 history，下一轮就会一起发给模型
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})

        # 直观看到 messages 越来越长
        print(f"\n[当前对话已累积 {len(history)} 条消息]\n")


if __name__ == "__main__":
    main()
