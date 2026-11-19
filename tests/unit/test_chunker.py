"""课件切块纯逻辑测试。"""

from aurora.chunker import chunk_text


def test_chk_unit_001__cleans_fullwidth_private_use_and_table_characters():
    """CHK-UNIT-001：锁定全角数据损失，并验证 PUA 与表格字符清洗。"""
    chunks = chunk_text(
        "输出＝１５Ｖ，正常\n含有\ue000乱码但保留文字\n表格|内容\n\ue001|---|",
        source="synthetic.md",
    )

    assert len(chunks) == 1
    assert chunks[0].text == "输出正常 含有乱码但保留文字 表格 内容"
    assert chunks[0].source == "synthetic.md"
    assert chunks[0].heading == ""


def test_chk_unit_002__filters_unreadable_and_placeholder_lines():
    """CHK-UNIT-002：只保留含汉字且不是模板占位内容的行。"""
    chunks = chunk_text(
        "English only\nE = mc^2\n请替换文字：课程标题\n有效中文 line 42"
    )

    assert [chunk.text for chunk in chunks] == ["有效中文 line 42"]
