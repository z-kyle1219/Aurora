"""EVL-META-001：裁判与人工双标数据的一致性（在线 + AI Evaluation）。

这条用例是唯一会真实调用裁判模型的元评估：它同时标记 `online`（需要真实
OpenAI 兼容 API）与 `ai_eval`（概率性评估，不属于软件回归），默认
`uv run pytest -q` 的 marker 过滤会把它排除，不会产生任何网络请求或费用。

运行方式（必须显式提供进程环境中的 key，不使用 .env）：

    DEEPSEEK_API_KEY=sk-... uv run pytest -q -o addopts="" -m "online and ai_eval"

断言只覆盖「报告可计算、结构完整、取值范围合法」，**不设任何质量百分比
门槛**：门槛要等这批一致性数据经人工确认后再单独决定。
"""

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from openai import OpenAI

from aurora import judge_meta
from aurora.config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from tests.conftest import TEST_API_KEY

pytestmark = [pytest.mark.online, pytest.mark.ai_eval]

DATASET = Path(__file__).resolve().parents[2] / "data" / "eval" / "meta" / "judge_agreement_v1.json"


def test_evl_meta_001__judge_agrees_with_human_double_annotation(tmp_path):
    """EVL-META-001：40 条人工双标样本上报告 agreement / Cohen's κ / 每类 precision-recall，
    并单列同源模型偏差。

    依赖：真实裁判 LLM + 人工双标数据集；不 Mock。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key or api_key == TEST_API_KEY:
        pytest.fail(
            "EVL-META-001 需要显式提供真实 DEEPSEEK_API_KEY：不接受测试 dummy key，也不读取 .env。"
            '运行命令：DEEPSEEK_API_KEY=... uv run pytest -q -o addopts="" -m "online and ai_eval"'
        )

    cases = judge_meta.load_dataset(DATASET)
    if not cases.is_human_double_annotated:
        pytest.fail(
            "EVL-META-001 的数据集标注状态为 "
            f"{cases.status}：两版标签都是 AI 起草的草稿，没有两位标注者独立标注。"
            "在这种状态下跑出的 agreement 会被误当成裁判可信度，因此用例直接失败。"
            "请先完成两位标注者的独立标注（annotation.annotators 至少两人），"
            "把 annotation.status 改为 human_double_annotated 后重跑。"
        )
    dataset_cases = cases.cases
    # 数据契约：40 条、四类各 10 条，顺序按 4 条一组交错，便于 --limit 分层抽样
    assert len(dataset_cases) == 40
    assert Counter(case.category for case in dataset_cases) == {
        "correct": 10, "partial": 10, "wrong": 10, "hallucinated": 10,
    }
    assert {case.gold_source for case in dataset_cases} == {"consensus", "adjudicated"}

    client = judge_meta.UsageTrackingClient(
        OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    )
    report = judge_meta.evaluate_with_client(
        dataset_cases,
        client=client,
        judge_model=DEEPSEEK_MODEL,
        dataset=judge_meta.dataset_summary(cases),
        base_url=DEEPSEEK_BASE_URL,
    )

    # ---- 结构契约：指标齐全、可序列化、取值范围合法（不是质量门槛）----
    markdown = judge_meta.format_report(report)
    assert "同源模型偏差" in markdown
    artifact = tmp_path / "evl_meta_001.json"
    artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert report["dataset"]["annotation_status"] == judge_meta.ANNOTATION_HUMAN
    assert len(report["dataset"]["annotators"]) >= 2

    judgment = report["answer_judgment"]
    assert judgment["n"] == len(dataset_cases)
    assert 0.0 <= judgment["agreement"] <= 1.0
    assert judgment["kappa"] is None or -1.0 <= judgment["kappa"] <= 1.0
    assert tuple(judgment["per_class"]) == judge_meta.JUDGMENTS
    assert all(
        metric["precision"] is None or 0.0 <= metric["precision"] <= 1.0
        for metric in judgment["per_class"].values()
    )
    assert sum(sum(row.values()) for row in judgment["confusion"].values()) == len(dataset_cases)

    hallucination = report["hallucination"]
    assert hallucination["n"] == len(dataset_cases)
    assert 0.0 <= hallucination["agreement"] <= 1.0
    assert hallucination["kappa"] is None or -1.0 <= hallucination["kappa"] <= 1.0
    assert set(hallucination["per_class"]) == {"False", "True"}

    same_source = report["same_source"]
    assert set(same_source["groups"]) == {"same", "different"}
    assert sum(same_source["relation_counts"].values()) == len(dataset_cases)
    assert same_source["judge_family"] == judge_meta.model_family(DEEPSEEK_MODEL)

    # 裁判自身解析失败必须可见：默认值不能悄悄混进一致性统计
    assert report["judge_errors"]["count"] == len(report["judge_errors"]["cases"])
    assert report["judge"]["calls"] == 2 * len(dataset_cases)
    assert client.usage["calls"] == 2 * len(dataset_cases)

    print(markdown)
