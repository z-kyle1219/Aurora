"""裁判元评估统计逻辑的离线单元测试（EVL-UNIT-004 ~ EVL-UNIT-008）。

这些用例只测纯函数与报告组装：不联网、不加载模型、不需要 API key，
也不读 `.env`。它们保护 `EVL-META-001` 的统计口径——那个在线用例本身
只在标注状态为 `human_double_annotated` 时才允许执行。
"""

import copy
import json

import pytest

from aurora import judge_meta


def _case_payload(case_id: str = "c1", **overrides) -> dict:
    payload = {
        "id": case_id,
        "category": "correct",
        "question": "问题",
        "reference_answer": "标准答案",
        "reference_context": "参考片段",
        "candidate_answer": "候选回答",
        "candidate_model": "deepseek-chat",
        "annotator_a": {"judgment": "correct", "hallucinated": False, "note": ""},
        "annotator_b": {"judgment": "correct", "hallucinated": False, "note": ""},
    }
    payload.update(overrides)
    return payload


def _payload() -> dict:
    return {
        "dataset": "unit-test",
        "annotation": {
            "status": judge_meta.ANNOTATION_DRAFT,
            "annotators": [],
            "note": "单元测试数据",
        },
        "cases": [_case_payload()],
    }


def _bare_array(payload: dict) -> dict | list:
    return payload["cases"]


def _drop_annotation(payload: dict) -> dict:
    payload.pop("annotation")
    return payload


def _bad_status(payload: dict) -> dict:
    payload["annotation"]["status"] = "done"
    return payload


def _single_annotator(payload: dict) -> dict:
    payload["annotation"].update(status=judge_meta.ANNOTATION_HUMAN, annotators=["alice"])
    return payload


def _duplicate_annotator(payload: dict) -> dict:
    payload["annotation"].update(
        status=judge_meta.ANNOTATION_HUMAN, annotators=["alice", "alice"],
    )
    return payload


def _whitespace_duplicate_annotator(payload: dict) -> dict:
    payload["annotation"].update(
        status=judge_meta.ANNOTATION_HUMAN, annotators=["alice", "  alice  "],
    )
    return payload


def _draft_with_annotators(payload: dict) -> dict:
    payload["annotation"]["annotators"] = ["alice", "bob"]
    return payload


def _missing_adjudication(payload: dict) -> dict:
    payload["cases"][0]["annotator_b"]["judgment"] = "wrong"
    return payload


def _redundant_adjudication(payload: dict) -> dict:
    payload["cases"][0]["adjudication"] = {
        "judgment": "correct", "hallucinated": False, "reviewer": "r",
    }
    return payload


def _bad_judgment(payload: dict) -> dict:
    payload["cases"][0]["annotator_a"]["judgment"] = "maybe"
    return payload


def _int_as_bool(payload: dict) -> dict:
    payload["cases"][0]["annotator_a"]["hallucinated"] = 0
    return payload


def _duplicate_id(payload: dict) -> dict:
    payload["cases"].append(copy.deepcopy(payload["cases"][0]))
    return payload


def _empty_cases(payload: dict) -> dict:
    payload["cases"] = []
    return payload


def _empty_text(payload: dict) -> dict:
    payload["cases"][0]["candidate_answer"] = "  "
    return payload


INVALID_VARIANTS = [
    ("bare-array", _bare_array, "顶层必须是对象"),
    ("no-annotation", _drop_annotation, "annotation"),
    ("bad-status", _bad_status, "annotation.status"),
    ("one-annotator", _single_annotator, "至少两位不同"),
    ("duplicate-annotator", _duplicate_annotator, "至少两位不同"),
    ("whitespace-duplicate-annotator", _whitespace_duplicate_annotator, "至少两位不同"),
    ("draft-with-annotators", _draft_with_annotators, "不应列出标注者"),
    ("no-adjudication", _missing_adjudication, "必须填 adjudication"),
    ("redundant-adjudication", _redundant_adjudication, "不应再填 adjudication"),
    ("bad-judgment", _bad_judgment, "judgment"),
    ("int-as-bool", _int_as_bool, "bool"),
    ("duplicate-id", _duplicate_id, "重复"),
    ("empty-cases", _empty_cases, "cases"),
    ("empty-text", _empty_text, "candidate_answer"),
]


def test_evl_unit_004__dataset_carrier_requires_annotation_status_and_consistent_labels(tmp_path):
    """EVL-UNIT-004：数据集承载格式与标注状态必须显式且自洽，非法输入一律 ValueError。"""
    for variant, mutate, fragment in INVALID_VARIANTS:
        path = tmp_path / f"{variant}.json"
        path.write_text(json.dumps(mutate(_payload()), ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            judge_meta.load_dataset(path)
        assert fragment in str(excinfo.value), (variant, str(excinfo.value))

    # 合法：草稿状态可加载，但不允许被当成人工双标数据
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    draft = judge_meta.load_dataset(draft_path)
    assert draft.status == judge_meta.ANNOTATION_DRAFT
    assert draft.is_human_double_annotated is False
    assert [case.gold_source for case in draft.cases] == ["consensus"]
    summary = judge_meta.dataset_summary(draft)
    assert summary["annotation_status"] == judge_meta.ANNOTATION_DRAFT
    assert summary["annotators"] == []
    assert summary["human_double_annotated"] is False

    # 合法：人工双标 + 双标不一致时的仲裁结果即 gold
    human_payload = _payload()
    human_payload["annotation"].update(
        status=judge_meta.ANNOTATION_HUMAN, annotators=["alice", "  bob "],
    )
    human_payload["cases"][0]["annotator_b"] = {
        "judgment": "wrong", "hallucinated": False, "note": "b",
    }
    human_payload["cases"][0]["adjudication"] = {
        "judgment": "partial", "hallucinated": False, "reviewer": "carol", "note": "仲裁",
    }
    human_path = tmp_path / "human.json"
    human_path.write_text(json.dumps(human_payload, ensure_ascii=False), encoding="utf-8")
    human = judge_meta.load_dataset(human_path)
    assert human.is_human_double_annotated is True
    assert human.cases[0].gold.judgment == "partial"
    assert human.cases[0].gold_source == "adjudicated"
    assert human.cases[0].annotators_agree is False
    human_summary = judge_meta.dataset_summary(human)
    assert human_summary["adjudicated"] == 1
    # 标注者名称去空白后再进报告，避免 "alice"/"alice " 被当成两个人
    assert human_summary["annotators"] == ["alice", "bob"]


def test_evl_unit_005__agreement_and_kappa_match_hand_computation():
    """EVL-UNIT-005：agreement 与 Cohen's κ 的手算口径，以及无定义时返回 None。"""
    pairs = [("correct", "correct"), ("correct", "wrong"), ("partial", "partial"), ("wrong", "wrong")]
    assert judge_meta.agreement_rate(pairs) == 0.75
    # po=0.75, pe=(2*1+1*1+1*2)/16=5/16 → κ=(0.75-0.3125)/(1-0.3125)
    assert judge_meta.cohens_kappa(pairs) == pytest.approx(7 / 11)
    assert judge_meta.cohens_kappa(pairs) == pytest.approx(0.6363636363636364)

    # 边际分布各占一半且 po 也是 0.5 → κ=0（随机水平），不能返回 None
    random_level = [
        ("correct", "correct"), ("correct", "partial"),
        ("partial", "correct"), ("partial", "partial"),
    ]
    assert judge_meta.agreement_rate(random_level) == 0.5
    assert judge_meta.cohens_kappa(random_level) == pytest.approx(0.0)

    # 完全一致但只有一个类别 → pe=1，κ 无定义
    degenerate = [("correct", "correct")] * 3
    assert judge_meta.agreement_rate(degenerate) == 1.0
    assert judge_meta.cohens_kappa(degenerate) is None

    # 空样本：不抛异常，也不伪造 0
    assert judge_meta.agreement_rate([]) is None
    assert judge_meta.cohens_kappa([]) is None


def test_evl_unit_006__per_class_metrics_and_confusion_matrix():
    """EVL-UNIT-006：每类 precision/recall/F1 的分母，以及混淆矩阵方向与总量。"""
    pairs = [("correct", "correct"), ("correct", "wrong"), ("partial", "partial"), ("wrong", "wrong")]
    metrics = judge_meta.per_class_metrics(pairs, judge_meta.JUDGMENTS)

    assert metrics["correct"] == {
        "support": 2, "tp": 1, "fp": 0, "fn": 1,
        "precision": 1.0, "recall": 0.5, "f1": pytest.approx(2 / 3),
    }
    assert (metrics["partial"]["precision"], metrics["partial"]["recall"]) == (1.0, 1.0)
    # wrong 的 precision 被 correct→wrong 的误判拉低（tp=1、fp=1），recall 仍为 1
    assert (metrics["wrong"]["precision"], metrics["wrong"]["recall"]) == (0.5, 1.0)

    # 某类从未被预测 → precision 无定义，但 recall 仍可算
    no_prediction = judge_meta.per_class_metrics([("correct", "wrong")], judge_meta.JUDGMENTS)
    assert no_prediction["correct"]["precision"] is None
    assert no_prediction["correct"]["recall"] == 0.0
    assert no_prediction["correct"]["f1"] is None

    # 某类没有 gold 样本 → recall 无定义，precision 可为 0（有预测但全错）
    no_support = judge_meta.per_class_metrics([("correct", "partial")], judge_meta.JUDGMENTS)
    assert no_support["partial"]["support"] == 0
    assert no_support["partial"]["recall"] is None
    assert no_support["partial"]["precision"] == 0.0

    matrix = judge_meta.confusion_matrix(pairs, judge_meta.JUDGMENTS)
    assert matrix["correct"] == {"correct": 1, "partial": 0, "wrong": 1}
    assert matrix["partial"]["partial"] == 1
    assert sum(sum(row.values()) for row in matrix.values()) == len(pairs)


def _make_case(case_id: str, *, judgment: str, hallucinated: bool = False,
               candidate_model: str = "deepseek-chat") -> judge_meta.MetaCase:
    annotation = judge_meta.Annotation(judgment=judgment, hallucinated=hallucinated)
    return judge_meta.MetaCase(
        id=case_id,
        category="hallucinated" if hallucinated else judgment,
        question="问题",
        reference_answer="标准答案",
        reference_context="参考片段",
        candidate_answer="候选回答",
        candidate_model=candidate_model,
        annotator_a=annotation,
        annotator_b=annotation,
        adjudication=None,
    )


def _perfect_judges():
    def answer(case: judge_meta.MetaCase) -> dict:
        return {"judgment": case.gold.judgment, "reason": "stub"}

    def hallucination(case: judge_meta.MetaCase) -> dict:
        return {"hallucinated": case.gold.hallucinated, "reason": "stub"}

    return answer, hallucination


def test_evl_unit_007__same_source_breakdown_and_leniency():
    """EVL-UNIT-007：同源/异源分组、宽松度方向，以及「无法分离」提示的触发条件。"""
    cases = [
        _make_case("c1", judgment="correct"),
        _make_case("c2", judgment="partial", candidate_model="gpt-4o"),
        _make_case("c3", judgment="wrong"),
    ]
    answer, hallucination = _perfect_judges()
    perfect = judge_meta.run_meta_eval(
        cases, judge_model="deepseek-chat",
        judge_answer_fn=answer, judge_hallucination_fn=hallucination,
    )
    section = perfect["same_source"]
    assert section["judge_family"] == "deepseek"
    assert section["relation_counts"] == {"same": 2, "different": 1}
    assert section["groups"]["same"]["n"] == 2
    assert section["groups"]["same"]["models"] == ["deepseek-chat"]
    assert section["groups"]["different"]["models"] == ["gpt-4o"]
    assert section["groups"]["same"]["judgment_agreement"] == 1.0
    assert section["groups"]["same"]["leniency"] == 0.0
    # 两组都存在时不输出「无法分离」的说明
    assert section["notes"] == []

    # 全判 correct：宽松度为正（同源组 wrong→correct 为 +2）
    lenient = judge_meta.run_meta_eval(
        cases, judge_model="deepseek-chat",
        judge_answer_fn=lambda case: {"judgment": "correct", "reason": "stub"},
        judge_hallucination_fn=lambda case: {"hallucinated": False, "reason": "stub"},
    )
    assert lenient["same_source"]["groups"]["same"]["leniency"] == pytest.approx(1.0)
    # 全判 correct：三条样本的分数差为 0、+1、+2
    assert lenient["answer_judgment"]["leniency"] == pytest.approx(1.0)
    assert any("宽松度" in note for note in lenient["same_source"]["notes"])

    # 全部同源：异源组为 None，且必须显式说明无法分离
    same_only = [cases[0], cases[2]]
    all_same = judge_meta.run_meta_eval(
        same_only, judge_model="deepseek-chat",
        judge_answer_fn=answer, judge_hallucination_fn=hallucination,
    )
    assert all_same["same_source"]["relation_counts"] == {"same": 2, "different": 0}
    assert all_same["same_source"]["groups"]["different"] is None
    assert any("无法在单次运行内分离" in note for note in all_same["same_source"]["notes"])

    # 换裁判家族：全部落进异源组，同源组为 None 且不再声称无法分离
    cross = judge_meta.run_meta_eval(
        same_only, judge_model="gpt-4o-mini",
        judge_answer_fn=answer, judge_hallucination_fn=hallucination,
    )
    assert cross["same_source"]["relation_counts"] == {"same": 0, "different": 2}
    assert cross["same_source"]["groups"]["same"] is None
    assert cross["same_source"]["notes"] == []


def test_evl_unit_008__report_shape_usage_after_run_and_cost():
    """EVL-UNIT-008：报告字段齐全、usage 取调用结束后的值、费用只在给单价时估算。"""
    cases = [_make_case("c1", judgment="correct"), _make_case("c2", judgment="partial", hallucinated=True)]
    answer, hallucination = _perfect_judges()
    state = {"calls": 0}

    def counting_answer(case):
        state["calls"] += 1
        return answer(case)

    def counting_hallucination(case):
        state["calls"] += 1
        return hallucination(case)

    report = judge_meta.run_meta_eval(
        cases, judge_model="deepseek-chat",
        judge_answer_fn=counting_answer, judge_hallucination_fn=counting_hallucination,
        usage_provider=lambda: {
            "calls": state["calls"], "prompt_tokens": 100 * state["calls"],
            "completion_tokens": 10 * state["calls"],
        },
    )
    # usage 必须是运行结束后的快照；提前取会得到 0，这正是曾出现过的缺陷
    assert report["performance"]["usage"]["calls"] == 4
    assert report["cost"]["estimated"] is None
    assert report["cost"]["note"] == "未提供单价，仅记录 token 用量"

    for key in ("dataset", "answer_judgment", "hallucination", "same_source",
                "performance", "judge_errors", "cases"):
        assert key in report
    assert report["judge"]["calls"] == 4
    assert report["judge_errors"] == {"count": 0, "cases": []}
    assert report["hallucination"]["false_negative_rate"] == 0.0
    assert report["hallucination"]["false_positive_rate"] == 0.0

    false_positive = judge_meta.run_meta_eval(
        cases, judge_model="deepseek-chat",
        judge_answer_fn=answer,
        judge_hallucination_fn=lambda case: {"hallucinated": True, "reason": "stub"},
    )
    # 一条非幻觉样本被误报，一条真实幻觉样本命中：FPR=1/1，不能误取 False 类的 fp。
    assert false_positive["hallucination"]["false_positive_rate"] == 1.0
    assert false_positive["hallucination"]["false_negative_rate"] == 0.0

    priced = judge_meta.run_meta_eval(
        cases, judge_model="deepseek-chat",
        judge_answer_fn=answer, judge_hallucination_fn=hallucination,
        usage_provider=lambda: {"calls": 4, "prompt_tokens": 400, "completion_tokens": 40},
        prices={"input_per_million": 0.15, "output_per_million": 0.60, "currency": "USD"},
    )
    assert priced["cost"]["estimated"] == pytest.approx(400 / 1e6 * 0.15 + 40 / 1e6 * 0.60)

    # 草稿标签：报告必须自己声明「不能当结论」，不能只靠读者记得
    draft_markdown = judge_meta.format_report(report)
    assert "标注未完成人工双标" in draft_markdown
    assert "标注草稿一致性" in draft_markdown
    assert "同源模型偏差" in draft_markdown
    assert "不设任何质量百分比门槛" in draft_markdown
    # gold 口径必须跟着标注状态走，不能草稿态还写「人工 gold」
    assert "裁判 vs 草稿 gold" in draft_markdown
    assert "裁判 vs 人工 gold" not in draft_markdown

    human_report = judge_meta.run_meta_eval(
        cases, judge_model="deepseek-chat",
        judge_answer_fn=answer, judge_hallucination_fn=hallucination,
        dataset={"annotation_status": judge_meta.ANNOTATION_HUMAN,
                 "annotators": ["alice", "bob"], "human_double_annotated": True,
                 "cases": 2, "version": "deadbeef0000", "path": "x.json",
                 "by_category": {"correct": 1, "partial": 1}, "adjudicated": 0},
    )
    human_markdown = judge_meta.format_report(human_report)
    assert "人工双标基线" in human_markdown
    assert "标注未完成人工双标" not in human_markdown
    assert "裁判 vs 人工 gold" in human_markdown
    assert "草稿 gold" not in human_markdown
