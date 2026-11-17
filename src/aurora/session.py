"""会话状态层 —— 让 history 有唯一的归属和唯一的写入点。

为什么需要这一层（而不是让调用方自己维护一个 list）：

1. 状态所有权：调用方持有的是一个 Session 对象，不是数据本体。它不需要
   知道历史存在哪、什么时候截断、格式该长什么样。
2. 单一数据源：`add_turn()` 是唯一写入点。将来要加历史截断、摘要压缩、
   落盘持久化，全都在一两个方法里改，不会散落到每个调用方。
3. 可扩展：W5 的教学闭环要往里加提示等级、尝试次数、当前知识点。
   加字段不会惊动任何调用方 —— 这就是"参数对象"比"散装参数"值钱的地方。

注意「一个字段 + 两个方法」这个形状：
    - 字段 history：数据本体
    - add_turn()：唯一写入点（有行为的位置）
    - context()：唯一读出口（将来做窗口截断的位置）

预留扩展点 = 留一个有行为的方法，而不是留一个永远为 None 的空字段。
"""

from dataclasses import dataclass, field


@dataclass
class Session:
    """一次教学会话的状态。

    现在只装 history。将来 W5 往里加：
        kp_id       当前练的知识点
        hint_level  提示等级 L0~L4
        attempts    当前这道题的尝试次数
    加这些字段时，pipeline.run 的签名一个字都不用改。
    """

    history: list[dict] = field(default_factory=list)

    def add_turn(self, question: str, answer: str) -> None:
        """把这一轮的问答写进历史 —— 全项目唯一的写入点。

        为什么要成对写：模型下一轮看到的 messages 必须是
        「user, assistant, user, assistant」交替的；只写一半，
        接口会拒绝，或者模型把两条 user 连在一起理解成一个人说了两次。
        """
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})

    def context(self) -> list[dict]:
        """给模型看的历史 —— 全项目唯一的读出口。

        现在原样返回全量。将来对话变长，要在这里做「只留最近 N 轮」的
        窗口截断，或者更高级的摘要压缩 —— 改这一个方法，所有链路自动生效。

        注意：这里返回的是 history 本身（不是副本），调用方只读不改。
        """
        return self.history

    def __len__(self) -> int:
        """打印/断言时能直接 len(session)，看历史累积了几条。"""
        return len(self.history)
