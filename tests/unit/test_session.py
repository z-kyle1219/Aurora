"""进程内 Session 纯逻辑测试。"""

from aurora.session import Session


def test_ses_unit_001__stores_turns_in_order_and_isolates_instances():
    """SES-UNIT-001：默认历史为空，问答成对有序写入且实例互不共享。"""
    first = Session()
    second = Session()

    assert first.context() == []
    assert second.context() == []
    assert len(first) == len(second) == 0

    first.add_turn("问题一", "回答一")
    second.add_turn("问题二", "回答二")

    assert first.context() == [
        {"role": "user", "content": "问题一"},
        {"role": "assistant", "content": "回答一"},
    ]
    assert second.context() == [
        {"role": "user", "content": "问题二"},
        {"role": "assistant", "content": "回答二"},
    ]
    assert len(first) == len(second) == 2

    first.add_turn("追问", "追答")

    assert len(first) == 4
    assert len(second) == 2
