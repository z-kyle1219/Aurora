"""公式卡片解析测试。"""

from aurora.card_loader import load_cards


def test_crd_unit_001__parses_multiple_cards_from_temporary_markdown(tmp_path):
    """CRD-UNIT-001：前言与空行不影响多卡字段、来源和顺序。"""
    cards_file = tmp_path / "cards.md"
    cards_file.write_text(
        "# 前言\n不会形成卡片\n\n"
        "## 虚短虚断\n关键词：虚短、虚断\n\n解释：理想运放规则\n"
        "## 反相比例\n公式：uo=-Rf/R1*ui\n条件：负反馈\n"
        "## 稳压管\n关键词：稳压\n来源：合成数据\n",
        encoding="utf-8",
    )

    chunks = load_cards(cards_file)

    assert [(chunk.heading, chunk.source, chunk.text) for chunk in chunks] == [
        (
            "虚短虚断",
            "公式卡片：虚短虚断",
            "虚短虚断。关键词：虚短、虚断 解释：理想运放规则",
        ),
        (
            "反相比例",
            "公式卡片：反相比例",
            "反相比例。公式：uo=-Rf/R1*ui 条件：负反馈",
        ),
        (
            "稳压管",
            "公式卡片：稳压管",
            "稳压管。关键词：稳压 来源：合成数据",
        ),
    ]
