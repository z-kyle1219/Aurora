"""跑一个最小示例：向学伴 v0 问一道电路题。

用法（在 aurora 目录下）：
    uv run scripts/demo_companion.py
"""

from aurora.companion_v0 import ask_stream

if __name__ == "__main__":
    question = "什么是集成运放的虚短和虚断？为什么理想运放可以做这样的近似？"

    print("问：", question)
    print("=" * 60)

    # 流式输出，逐段打印，体验更接近真实聊天
    for piece in ask_stream(question):
        print(piece, end="", flush=True)
    print()
