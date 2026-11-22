"""裁判元评估（EVL-META-001）——用人工双标数据检验 LLM 裁判本身可不可信。

为什么需要
----------
`scripts/eval_report.py` 里 DeepSeek 既当考生又当裁判，答案正确率和幻觉率
都是「模型自评」的产物。没有人工标签对齐，这两个数字无法解释：判分偏宽会
把错误答成 correct，偏严会把正确答成 partial，而报告里看不出是哪一种。

本模块让裁判去判一批「已双标并仲裁」的固定样本，输出裁判与人工标签的
一致性。它不产出能力分数，只回答一个问题：**后面那些质量指标，值不值得信。**

当前数据状态以数据集 `annotation.status` 为准；`judge_agreement_v1` 已由两位人工
标注者独立核验。加载器仍保留草稿状态保护，避免未来新增草稿数据被误当成人工
一致性结论。

数据集承载格式
--------------
`data/eval/meta/judge_agreement_v1.json`：一个对象，`annotation` 声明标注状态，
`cases` 是样本数组。

    {
      "dataset": "judge_agreement_v1",
      "annotation": {
        "status": "ai_drafted_pending_human_double_annotation",
        "annotators": [],
        "note": "……"
      },
      "cases": [
        {
          "id": "meta-001",                     # 稳定编号，全文件唯一
          "category": "correct",                # correct|partial|wrong|hallucinated，仅用于覆盖度
          "question": "...",                    # 学生原始问题
          "reference_answer": "...",            # 标准答案（judge_answer 的对照）
          "reference_context": "...",           # 参考课件片段（judge_hallucination 的对照）
          "candidate_answer": "...",            # 被评回答
          "candidate_model": "deepseek-chat",   # 生成该回答的模型，用于同源偏差分组
          "annotator_a": {"judgment": "correct", "hallucinated": false, "note": "..."},
          "annotator_b": {"judgment": "correct", "hallucinated": false, "note": "..."},
          "adjudication": {"judgment": "partial", "hallucinated": true,
                           "reviewer": "...", "note": "..."}
        }
      ]
    }

`annotation.status` 只有两个合法值，它决定这份数据能得出什么结论：

- `ai_drafted_pending_human_double_annotation`：`annotator_a/annotator_b` 是**同一
  作者的标注草稿**，不存在两位独立标注者，`adjudication.reviewer` 只是占位标记。
  此状态下 agreement 只反映「裁判与草稿的差异」，不能当作裁判可信度，也不能
  用来定质量门槛；在线用例在该状态直接失败。
- `human_double_annotated`：`annotators` 必须列出至少两位**不同**的标注者（去空白后
  比较，`["alice", "alice"]` 视为同一人而不通过），此时 `annotator_a/annotator_b`
  才代表两人的独立标注，`human_baseline` 才成立。

单条记录的规则（`load_dataset` 强制校验，违反即报错，不做静默修补）：

- `judgment` 只能是 `correct/partial/wrong`；`hallucinated` 必须是真正的 bool
  （`0`/`1`/`"true"` 一律拒绝，避免把标注噪声带进 κ）；
- 两位标注者两项都一致时**不得**填 `adjudication`，一致结果即 gold；
- 任一项不一致时**必须**填 `adjudication`，仲裁结果即 gold；
- `category` 只是采样分层标签，所有指标以仲裁后的 gold 为准，不按 category 分组
  算准确率（否则等于用标注分类给裁判送分）。

指标
----
- **agreement**：裁判判分与 gold 完全一致的比例；
- **Cohen's κ**：三档判分算一次、幻觉二分类算一次，扣除随机一致；
  期望不一致为 0 时返回 `None`（标记为「不可计算」而不是伪造 0）；
- **各类 precision/recall/F1**：以 gold 为分母的混淆矩阵，某类无预测/无样本时
  对应指标为 `None`；
- **同源模型偏差单列**：按「裁判模型家族 vs `candidate_model` 家族」分组，
  分别给出一致性、κ、宽松度（judge 分数 − gold 分数均值，correct=2/partial=1/
  wrong=0）与幻觉漏报率。同源组指标偏高且宽松度为正，就是「自己人当裁判」
  的量化信号。异源对照不需要额外代码：换 `--model/--base-url` 再跑一次，
  第二次报告里这批样本会自动落进 `different` 组。

边界
----
- 不设任何质量百分比门槛，只出报告；判定留给人；
- 不读 `.env`、不隐式联网：入口一律要求调用方显式传入 API key；
- 不读取本地索引：参考片段由数据集自带，报告里 `index` 字段固定为 `None`，
  不伪造索引 hash。

离线可做与不可做
----------------
`--validate-only` 只读数据集并校验格式，不导入 `aurora.config`（因而不触碰
`.env`）、不联网、不需要凭据，可作为付费运行前的预检。
"""

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 与 aurora.judge.JUDGMENTS 必须一致；此处不导入 judge，是为了让数据集校验
# 路径完全不依赖 config（导入 config 会触发 load_dotenv 读取 .env）。
JUDGMENTS = ("correct", "partial", "wrong")
META_CATEGORIES = ("correct", "partial", "wrong", "hallucinated")
JUDGMENT_SCORES = {"correct": 2, "partial": 1, "wrong": 0}

# 标注状态：决定 agreement 能否解释为「裁判与人工的一致性」。
ANNOTATION_DRAFT = "ai_drafted_pending_human_double_annotation"
ANNOTATION_HUMAN = "human_double_annotated"
ANNOTATION_STATUSES = (ANNOTATION_DRAFT, ANNOTATION_HUMAN)

# judge.py 在两个解析失败分支返回的固定 reason，用于统计「裁判自身失效」次数。
_JUDGE_ERROR_REASONS = ("裁判未返回有效判分", "裁判参数解析失败")


@dataclass(frozen=True)
class Annotation:
    """一份人工标注：三档判分 + 幻觉布尔 + 备注。"""

    judgment: str
    hallucinated: bool
    note: str = ""


@dataclass(frozen=True)
class MetaCase:
    """元评估的一条样本，含双标与可选仲裁。"""

    id: str
    category: str
    question: str
    reference_answer: str
    reference_context: str
    candidate_answer: str
    candidate_model: str
    annotator_a: Annotation
    annotator_b: Annotation
    adjudication: Annotation | None = None

    @property
    def gold(self) -> Annotation:
        """gold 标签：有仲裁用仲裁，否则用一致结果（= 标注者 A）。"""
        return self.adjudication or self.annotator_a

    @property
    def gold_source(self) -> str:
        return "adjudicated" if self.adjudication else "consensus"

    @property
    def annotators_agree(self) -> bool:
        return _same_annotation(self.annotator_a, self.annotator_b)


@dataclass(frozen=True)
class MetaDataset:
    """一份元评估数据集：标注状态声明 + 样本列表。"""

    path: Path
    annotation: dict
    cases: list[MetaCase]

    @property
    def status(self) -> str:
        return self.annotation["status"]

    @property
    def is_human_double_annotated(self) -> bool:
        """只有两位标注者独立标注过，`human_baseline` 才有「上界」含义。"""
        return self.status == ANNOTATION_HUMAN


def _same_annotation(left: Annotation, right: Annotation) -> bool:
    return left.judgment == right.judgment and left.hallucinated == right.hallucinated


def _require_text(raw: dict, key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: 字段 {key} 必须是非空字符串")
    return value


def _annotation_from(raw: Any, where: str, field: str) -> Annotation:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: 字段 {field} 必须是对象")
    judgment = raw.get("judgment")
    if judgment not in JUDGMENTS:
        raise ValueError(f"{where}: {field}.judgment 必须是 {JUDGMENTS} 之一，实际为 {judgment!r}")
    hallucinated = raw.get("hallucinated")
    if not isinstance(hallucinated, bool):
        raise ValueError(f"{where}: {field}.hallucinated 必须是 bool，实际为 {hallucinated!r}")
    note = raw.get("note", "")
    if not isinstance(note, str):
        raise ValueError(f"{where}: {field}.note 必须是字符串")
    return Annotation(judgment=judgment, hallucinated=hallucinated, note=note)


def _build_cases(raw_cases: list, path: Path) -> list[MetaCase]:
    """逐条校验样本，任何格式或双标一致性问题都直接抛 ValueError。"""
    cases: list[MetaCase] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_cases, 1):
        where = f"{path.name} 第 {index} 条"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: 必须是对象")
        case_id = _require_text(item, "id", where)
        if case_id in seen:
            raise ValueError(f"{where}: id {case_id!r} 重复")
        seen.add(case_id)
        category = item.get("category")
        if category not in META_CATEGORIES:
            raise ValueError(f"{where}: category 必须是 {META_CATEGORIES} 之一，实际为 {category!r}")

        annotator_a = _annotation_from(item.get("annotator_a"), where, "annotator_a")
        annotator_b = _annotation_from(item.get("annotator_b"), where, "annotator_b")
        adjudication_raw = item.get("adjudication")
        adjudication = (
            None
            if adjudication_raw is None
            else _annotation_from(adjudication_raw, where, "adjudication")
        )
        agree = _same_annotation(annotator_a, annotator_b)
        if agree and adjudication is not None:
            raise ValueError(f"{where}: 两位标注者已一致，不应再填 adjudication")
        if not agree and adjudication is None:
            raise ValueError(f"{where}: 两位标注者不一致，必须填 adjudication（人工仲裁）")
        if adjudication is not None:
            reviewer = adjudication_raw.get("reviewer") if isinstance(adjudication_raw, dict) else None
            if not isinstance(reviewer, str) or not reviewer.strip():
                raise ValueError(f"{where}: adjudication.reviewer 必须是非空字符串")

        cases.append(
            MetaCase(
                id=case_id,
                category=category,
                question=_require_text(item, "question", where),
                reference_answer=_require_text(item, "reference_answer", where),
                reference_context=_require_text(item, "reference_context", where),
                candidate_answer=_require_text(item, "candidate_answer", where),
                candidate_model=_require_text(item, "candidate_model", where),
                annotator_a=annotator_a,
                annotator_b=annotator_b,
                adjudication=adjudication,
            )
        )
    return cases


def load_dataset(path: Path) -> MetaDataset:
    """读取并校验元评估数据集，含顶层标注状态声明。

    标注状态不是可选的元数据：它决定 agreement 能否被解释为「裁判与人工的
    一致性」。缺 `annotation` 或状态非法一律报错，不允许用裸数组加载——那样
    调用方就无从知道标签是人工双标还是 AI 草稿。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"数据集不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: 不是合法 JSON：{exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: 顶层必须是对象（含 annotation 与 cases）")
    annotation = raw.get("annotation")
    if not isinstance(annotation, dict):
        raise ValueError(f"{path.name}: 缺少 annotation 对象（标注状态声明）")
    status = annotation.get("status")
    if status not in ANNOTATION_STATUSES:
        raise ValueError(
            f"{path.name}: annotation.status 必须是 {ANNOTATION_STATUSES} 之一，实际为 {status!r}"
        )
    annotators = annotation.get("annotators", [])
    if not isinstance(annotators, list) or not all(
        isinstance(name, str) and name.strip() for name in annotators
    ):
        raise ValueError(f"{path.name}: annotation.annotators 必须是字符串数组")
    # 数量够两位不等于「两个人」：["alice", "alice"] 是同一人标了两次，
    # 不能算独立双标。按去空白后的唯一姓名判断，避免这类数据被放行。
    unique_annotators = {name.strip() for name in annotators}
    if status == ANNOTATION_HUMAN and len(unique_annotators) < 2:
        raise ValueError(
            f"{path.name}: status=human_double_annotated 时必须列出至少两位不同的标注者"
            f"（去空白后比较），实际为 {annotators!r}"
        )
    if status == ANNOTATION_DRAFT and annotators:
        raise ValueError(
            f"{path.name}: status=ai_drafted_pending_human_double_annotation 时不应列出标注者，实际为 {annotators!r}"
        )

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"{path.name}: cases 必须是非空数组")
    return MetaDataset(path=path, annotation=annotation, cases=_build_cases(raw_cases, path))


def dataset_summary(dataset: MetaDataset) -> dict:
    """报告用的数据集元信息：版本（内容摘要）、条数、分层覆盖、标注状态。"""
    digest = hashlib.sha256(dataset.path.read_bytes()).hexdigest()
    return {
        "path": dataset.path.as_posix(),
        "version": digest[:12],
        "cases": len(dataset.cases),
        "by_category": dict(Counter(case.category for case in dataset.cases)),
        "adjudicated": sum(1 for case in dataset.cases if case.adjudication is not None),
        "annotation_status": dataset.status,
        "annotators": [name.strip() for name in dataset.annotation.get("annotators", [])],
        "annotation_note": dataset.annotation.get("note", ""),
        "human_double_annotated": dataset.is_human_double_annotated,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def agreement_rate(pairs: Sequence[tuple[Any, Any]]) -> float | None:
    """完全一致比例。"""
    if not pairs:
        return None
    return sum(1 for gold, pred in pairs if gold == pred) / len(pairs)


def cohens_kappa(pairs: Sequence[tuple[Any, Any]]) -> float | None:
    """Cohen's κ = (po − pe) / (1 − pe)。

    期望不一致为 0（全部样本落在同一类且预测也全对该类）时返回 None：
    此时 κ 在数学上无定义，写 0 或 1 都会误导。
    """
    n = len(pairs)
    if n == 0:
        return None
    gold_counts = Counter(gold for gold, _ in pairs)
    pred_counts = Counter(pred for _, pred in pairs)
    po = sum(1 for gold, pred in pairs if gold == pred) / n
    pe = sum(gold_counts[label] * pred_counts.get(label, 0) for label in gold_counts) / (n * n)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)


def confusion_matrix(pairs: Sequence[tuple[Any, Any]], labels: Sequence[Any]) -> dict:
    """行 = gold，列 = 裁判预测。"""
    matrix = {str(gold): {str(pred): 0 for pred in labels} for gold in labels}
    for gold, pred in pairs:
        matrix[str(gold)][str(pred)] += 1
    return matrix


def per_class_metrics(pairs: Sequence[tuple[Any, Any]], labels: Sequence[Any]) -> dict:
    """以 gold 为分母的每类 precision/recall/F1 与支持数。"""
    result = {}
    for label in labels:
        true_positive = sum(1 for gold, pred in pairs if gold == label and pred == label)
        false_positive = sum(1 for gold, pred in pairs if gold != label and pred == label)
        false_negative = sum(1 for gold, pred in pairs if gold == label and pred != label)
        precision = _rate(true_positive, true_positive + false_positive)
        recall = _rate(true_positive, true_positive + false_negative)
        f1 = None
        if precision is not None and recall is not None and precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
        result[str(label)] = {
            "support": sum(1 for gold, _ in pairs if gold == label),
            "tp": true_positive,
            "fp": false_positive,
            "fn": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return result


def model_family(model: str) -> str:
    """取模型名第一段作为「家族」，例如 deepseek-chat → deepseek。

    只用于把「同一个模型判自己生成的答案」和「换一家模型判」分开统计；
    命名不规则时退回完整名字，不做猜测。
    """
    head = (model or "").strip().lower().replace("_", "-").split("/")[-1].split("-")[0]
    return head or "unknown"


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _leniency(rows: Sequence[dict]) -> float | None:
    """宽松度 = 裁判分数 − gold 分数 的均值。正值表示裁判比 gold 更宽容。"""
    deltas = [
        JUDGMENT_SCORES[row["judged_judgment"]] - JUDGMENT_SCORES[row["gold_judgment"]]
        for row in rows
    ]
    return _mean([float(delta) for delta in deltas])


def _judgment_section(rows: Sequence[dict]) -> dict:
    pairs = [(row["gold_judgment"], row["judged_judgment"]) for row in rows]
    return {
        "n": len(pairs),
        "agreement": agreement_rate(pairs),
        "kappa": cohens_kappa(pairs),
        "per_class": per_class_metrics(pairs, JUDGMENTS),
        "confusion": confusion_matrix(pairs, JUDGMENTS),
        "gold_distribution": {label: sum(1 for gold, _ in pairs if gold == label) for label in JUDGMENTS},
        "judge_distribution": {label: sum(1 for _, pred in pairs if pred == label) for label in JUDGMENTS},
        "leniency": _leniency(rows),
    }


def _hallucination_section(rows: Sequence[dict]) -> dict:
    pairs = [(row["gold_hallucinated"], row["judged_hallucinated"]) for row in rows]
    per_class = per_class_metrics(pairs, (False, True))
    positive = per_class["True"]
    negative = per_class["False"]
    return {
        "n": len(pairs),
        "agreement": agreement_rate(pairs),
        "kappa": cohens_kappa(pairs),
        "per_class": per_class,
        "confusion": confusion_matrix(pairs, (False, True)),
        "false_negative_rate": _rate(positive["fn"], positive["support"]),
        "false_positive_rate": _rate(positive["fp"], negative["support"]),
    }


def _group_section(rows: Sequence[dict], cases_by_id: dict[str, MetaCase]) -> dict:
    """同一组（同源 / 异源）内的一致性、宽松度与幻觉漏报率。"""
    pairs = [(row["gold_judgment"], row["judged_judgment"]) for row in rows]
    hallucination_pairs = [(row["gold_hallucinated"], row["judged_hallucinated"]) for row in rows]
    hallucinated_support = sum(1 for gold, _ in hallucination_pairs if gold is True)
    missed = sum(1 for gold, pred in hallucination_pairs if gold is True and pred is False)
    return {
        "n": len(rows),
        "models": sorted({cases_by_id[row["id"]].candidate_model for row in rows}),
        "judgment_agreement": agreement_rate(pairs),
        "judgment_kappa": cohens_kappa(pairs),
        "hallucination_agreement": agreement_rate(hallucination_pairs),
        "hallucination_kappa": cohens_kappa(hallucination_pairs),
        "hallucination_false_negative_rate": _rate(missed, hallucinated_support),
        "leniency": _leniency(rows),
    }


def same_source_section(rows: Sequence[dict], cases: Sequence[MetaCase], judge_model: str) -> dict:
    """把样本按「裁判家族 == 生成模型家族」拆成同源组与异源组，单列同源偏差。"""
    cases_by_id = {case.id: case for case in cases}
    judge_family = model_family(judge_model)
    groups: dict[str, list[dict]] = {"same": [], "different": []}
    for row in rows:
        relation = (
            "same"
            if model_family(cases_by_id[row["id"]].candidate_model) == judge_family
            else "different"
        )
        row["source_relation"] = relation
        groups[relation].append(row)

    breakdown = {
        relation: (_group_section(group_rows, cases_by_id) if group_rows else None)
        for relation, group_rows in groups.items()
    }
    notes: list[str] = []
    if not groups["different"]:
        notes.append(
            "本次全部样本的生成模型与裁判同源，同源偏差无法在单次运行内分离；"
            "需换 --model/--base-url 跑一次异源对照，或只把同源组当作偏乐观的上界。"
        )
    same = breakdown["same"]
    if same is not None and same["leniency"] is not None and same["leniency"] > 0:
        notes.append(
            f"同源组宽松度为 +{same['leniency']:.2f}（满分差 2 分），裁判对同源回答偏宽容。"
        )
    return {
        "judge_model": judge_model,
        "judge_family": judge_family,
        "relation_counts": {relation: len(group_rows) for relation, group_rows in groups.items()},
        "groups": breakdown,
        "notes": notes,
    }


def human_baseline_section(cases: Sequence[MetaCase]) -> dict:
    """两位标注者之间的一致性。

    仅当标注状态为 `human_double_annotated` 时才构成裁判 agreement 的上界；
    草稿状态下它只是同一作者两版草稿之间的差异，`format_report` 会改标题并声明。
    """
    judgment_pairs = [(case.annotator_a.judgment, case.annotator_b.judgment) for case in cases]
    hallucination_pairs = [
        (case.annotator_a.hallucinated, case.annotator_b.hallucinated) for case in cases
    ]
    disagreements = [case.id for case in cases if not case.annotators_agree]
    return {
        "n": len(cases),
        "judgment_agreement": agreement_rate(judgment_pairs),
        "judgment_kappa": cohens_kappa(judgment_pairs),
        "hallucination_agreement": agreement_rate(hallucination_pairs),
        "hallucination_kappa": cohens_kappa(hallucination_pairs),
        "disagreements": disagreements,
        "adjudicated": sum(1 for case in cases if case.adjudication is not None),
    }


def run_meta_eval(
    cases: Sequence[MetaCase],
    *,
    judge_model: str,
    judge_answer_fn: Callable[[MetaCase], dict],
    judge_hallucination_fn: Callable[[MetaCase], dict],
    dataset: dict | None = None,
    base_url: str | None = None,
    usage_provider: Callable[[], dict | None] | None = None,
    prices: dict | None = None,
) -> dict:
    """逐条调用裁判并汇总报告。

    裁判函数由调用方注入（`evaluate_with_client` 提供真实实现），因此本函数
    可以在不联网的情况下用替身验证指标计算；判定阈值不在这里，也不在任何
    调用方。

    `usage_provider` 必须是**取用量的回调**而不是用量本身：用量要等所有
    调用结束后才有值，提前快照会得到全 0。
    """
    rows: list[dict] = []
    started = time.perf_counter()
    for case in cases:
        case_started = time.perf_counter()
        judgment = judge_answer_fn(case)
        hallucination = judge_hallucination_fn(case)
        rows.append(
            {
                "id": case.id,
                "category": case.category,
                "gold_source": case.gold_source,
                "gold_judgment": case.gold.judgment,
                "judged_judgment": judgment["judgment"],
                "gold_hallucinated": case.gold.hallucinated,
                "judged_hallucinated": hallucination["hallucinated"],
                "judgment_reason": judgment.get("reason", ""),
                "hallucination_reason": hallucination.get("reason", ""),
                "latency": time.perf_counter() - case_started,
            }
        )
    elapsed = time.perf_counter() - started

    judge_errors = [
        row["id"]
        for row in rows
        if row["judgment_reason"] in _JUDGE_ERROR_REASONS
        or row["hallucination_reason"] in _JUDGE_ERROR_REASONS
    ]
    latencies = [row["latency"] for row in rows]
    usage = usage_provider() if usage_provider is not None else None

    return {
        "test_id": "EVL-META-001",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dataset": dataset,
        "index": None,
        "index_note": "EVL-META-001 的参考片段由数据集自带，不读取本地索引",
        "judge": {
            "model": judge_model,
            "base_url": base_url,
            "temperature": 0.0,
            "samples": 1,
            "calls": 2 * len(cases),
        },
        "human_baseline": human_baseline_section(cases),
        "answer_judgment": _judgment_section(rows),
        "hallucination": _hallucination_section(rows),
        "same_source": same_source_section(rows, cases, judge_model),
        "performance": {
            "total_seconds": elapsed,
            "mean_latency_seconds": _mean(latencies),
            "usage": usage,
        },
        "cost": _cost_section(usage, prices),
        "judge_errors": {"count": len(judge_errors), "cases": judge_errors},
        "cases": rows,
    }


def _cost_section(usage: dict | None, prices: dict | None) -> dict:
    """费用只在显式给出单价时估算；单价随官方调整，不写死在代码里。"""
    if not usage or not prices:
        return {"estimated": None, "prices": prices, "note": "未提供单价，仅记录 token 用量"}
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    estimated = (
        prompt / 1_000_000 * prices["input_per_million"]
        + completion / 1_000_000 * prices["output_per_million"]
    )
    return {"estimated": estimated, "prices": prices, "currency": prices.get("currency", "USD")}


class UsageTrackingClient:
    """包装真实客户端，累计 token 用量，其余调用原样透传。

    裁判函数只关心 `chat.completions.create`，所以这里只实现这条协议；
    累加 `resp.usage` 是为了让报告能给出 token 与费用，而不改动 `judge.py`
    的返回结构（JUD-UNIT-003 断言了返回 dict 的精确形状）。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self.calls = 0

    @property
    def chat(self) -> "UsageTrackingClient":
        return self

    @property
    def completions(self) -> "UsageTrackingClient":
        return self

    def create(self, **kwargs: Any) -> Any:
        response = self._inner.chat.completions.create(**kwargs)
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self._completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        return response

    @property
    def usage(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._prompt_tokens + self._completion_tokens,
        }


def make_judge_functions(client: Any, model: str) -> tuple[Callable[[MetaCase], dict], Callable[[MetaCase], dict]]:
    """把真实裁判包成 `run_meta_eval` 需要的两个回调。"""
    from aurora import judge

    if tuple(judge.JUDGMENTS) != JUDGMENTS:
        raise RuntimeError(
            f"judge.JUDGMENTS={judge.JUDGMENTS} 与元评估的 {JUDGMENTS} 不一致，"
            "改判分档位时必须同步修改数据集与指标"
        )

    def answer_fn(case: MetaCase) -> dict:
        return judge.judge_answer(
            case.reference_answer, case.candidate_answer, client=client, model=model
        )

    def hallucination_fn(case: MetaCase) -> dict:
        return judge.judge_hallucination(
            case.reference_context,
            case.candidate_answer,
            question=case.question,
            client=client,
            model=model,
        )

    return answer_fn, hallucination_fn


def evaluate_with_client(
    cases: Sequence[MetaCase],
    *,
    client: Any,
    judge_model: str,
    dataset: dict | None = None,
    base_url: str | None = None,
    prices: dict | None = None,
) -> dict:
    """用真实客户端跑完 40 条，返回报告 dict。"""
    answer_fn, hallucination_fn = make_judge_functions(client, judge_model)

    def _usage() -> dict | None:
        return getattr(client, "usage", None)

    return run_meta_eval(
        cases,
        judge_model=judge_model,
        judge_answer_fn=answer_fn,
        judge_hallucination_fn=hallucination_fn,
        dataset=dataset,
        base_url=base_url,
        usage_provider=_usage,
        prices=prices,
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def format_report(report: dict) -> str:
    """把报告 dict 渲染成可贴进 PR / 验收记录的 Markdown。"""
    lines: list[str] = ["# EVL-META-001 裁判一致性报告", ""]

    dataset = report.get("dataset") or {}
    human_labels = bool(dataset.get("human_double_annotated"))
    lines += [
        "## 1. 数据集",
        "",
        f"- 文件：`{dataset.get('path', '—')}`",
        f"- 版本（前 12 位 sha256）：`{dataset.get('version', '—')}`",
        f"- 条数：{dataset.get('cases', '—')}；分层：{dataset.get('by_category', {})}",
        f"- 标注状态：`{dataset.get('annotation_status', '—')}`；标注者：{dataset.get('annotators', [])}",
        f"- {'双标' if human_labels else '标注草稿'}不一致并仲裁：{dataset.get('adjudicated', '—')} 条",
        f"- 索引：{report.get('index_note', '不适用')}",
        "",
    ]
    if not human_labels:
        lines += [
            "> **标注未完成人工双标**：两版标签与仲裁均为同一作者的草稿，"
            "本报告的任何 agreement / κ 只反映裁判与草稿的差异，"
            "不能作为裁判可信度、答案正确率或质量门槛的依据。",
            "",
        ]
    lines += [
        "## 2. 人工双标基线（裁判 agreement 的上界参考）"
        if human_labels
        else "## 2. 标注草稿一致性（非人工双标，不能作为上界）",
        "",
        f"- 三档判分：agreement {_pct(report['human_baseline']['judgment_agreement'])}"
        f"，κ {_num(report['human_baseline']['judgment_kappa'])}",
        f"- 幻觉二分类：agreement {_pct(report['human_baseline']['hallucination_agreement'])}"
        f"，κ {_num(report['human_baseline']['hallucination_kappa'])}",
    ]
    disagreements = report["human_baseline"]["disagreements"]
    if disagreements:
        label = "双标不一致" if human_labels else "标注草稿不一致"
        lines.append(f"- {label}：{', '.join(disagreements)}（仲裁结果见数据集 `adjudication`）")
    gold_label = "人工 gold" if human_labels else "草稿 gold"
    lines += ["", f"## 3. 三档判分：裁判 vs {gold_label}", ""]

    judgment = report["answer_judgment"]
    lines += [
        f"- n = {judgment['n']}；agreement = {_pct(judgment['agreement'])}；"
        f"Cohen's κ = {_num(judgment['kappa'])}",
        f"- 宽松度（裁判分数 − gold 分数，correct=2/partial=1/wrong=0）：{_num(judgment['leniency'])}",
        f"- 分布：gold {judgment['gold_distribution']} / 裁判 {judgment['judge_distribution']}",
        "",
        "| 类别 | support | precision | recall | F1 |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in JUDGMENTS:
        metrics = judgment["per_class"][label]
        lines.append(
            f"| {label} | {metrics['support']} | {_pct(metrics['precision'])} | "
            f"{_pct(metrics['recall'])} | {_num(metrics['f1'])} |"
        )
    lines += ["", "混淆矩阵（行=gold，列=裁判）：", "", "| gold \\ 裁判 | " + " | ".join(JUDGMENTS) + " |"]
    lines.append("|---|" + "---:|" * len(JUDGMENTS))
    for gold in JUDGMENTS:
        cells = " | ".join(str(judgment["confusion"][gold][pred]) for pred in JUDGMENTS)
        lines.append(f"| {gold} | {cells} |")

    hallucination = report["hallucination"]
    lines += [
        "",
        f"## 4. 幻觉二分类：裁判 vs {gold_label}",
        "",
        f"- n = {hallucination['n']}；agreement = {_pct(hallucination['agreement'])}；"
        f"Cohen's κ = {_num(hallucination['kappa'])}",
        f"- hallucinated=True：precision {_pct(hallucination['per_class']['True']['precision'])}"
        f"，recall {_pct(hallucination['per_class']['True']['recall'])}",
        f"- 漏报率（真幻觉被判无）：{_pct(hallucination['false_negative_rate'])}；"
        f"误报率（无幻觉被判有）：{_pct(hallucination['false_positive_rate'])}",
        "",
        f"混淆矩阵（行=gold，列=裁判）：`{hallucination['confusion']}`",
        "",
        "## 5. 同源模型偏差（单列）",
        "",
        f"- 裁判模型：`{report['same_source']['judge_model']}`"
        f"（家族 `{report['same_source']['judge_family']}`）",
        f"- 分组样本数：{report['same_source']['relation_counts']}",
        "",
        "| 组 | n | 判分 agreement | 判分 κ | 幻觉 agreement | 幻觉漏报率 | 宽松度 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for relation, label in (("same", "同源"), ("different", "异源")):
        group = report["same_source"]["groups"][relation]
        if group is None:
            lines.append(f"| {label} | 0 | — | — | — | — | — |")
            continue
        lines.append(
            f"| {label} | {group['n']} | {_pct(group['judgment_agreement'])} | "
            f"{_num(group['judgment_kappa'])} | {_pct(group['hallucination_agreement'])} | "
            f"{_pct(group['hallucination_false_negative_rate'])} | {_num(group['leniency'])} |"
        )
    for note in report["same_source"]["notes"]:
        lines += ["", f"> {note}"]

    performance = report["performance"]
    cost = report.get("cost") or {}
    usage = performance.get("usage") or {}
    lines += [
        "",
        "## 6. 运行记录",
        "",
        f"- 模型：`{report['judge']['model']}`；base_url：`{report['judge']['base_url']}`；"
        f"temperature={report['judge']['temperature']}；采样数={report['judge']['samples']}",
        f"- 调用次数：{report['judge']['calls']}；总耗时 {performance['total_seconds']:.1f}s；"
        f"平均每题 {performance['mean_latency_seconds']:.2f}s"
        if performance.get("mean_latency_seconds") is not None
        else f"- 调用次数：{report['judge']['calls']}",
        f"- token：{usage if usage else '未采集'}",
    ]
    if cost.get("estimated") is None:
        lines.append(f"- 费用：未估算（{cost.get('note', '未提供单价')}）")
    else:
        lines.append(f"- 估算费用：{cost['estimated']:.4f} {cost.get('currency', 'USD')}")
    lines += [
        f"- 数据集版本：`{dataset.get('version', '—')}`；生成时间：{report['generated_at']}",
        "",
        "## 7. 需人工复核的样本",
        "",
    ]
    errors = report.get("judge_errors") or {}
    if errors.get("count"):
        lines.append(f"- 裁判自身解析失败（按默认值计入）：{errors['count']} 条 {errors['cases']}")
    else:
        lines.append("- 裁判解析失败：0 条")
    mismatched = [row["id"] for row in report["cases"] if row["gold_judgment"] != row["judged_judgment"]]
    lines.append(f"- 判分与 gold 不一致：{len(mismatched)} 条 {mismatched}")
    hallucination_mismatched = [
        row["id"] for row in report["cases"] if row["gold_hallucinated"] != row["judged_hallucinated"]
    ]
    lines.append(f"- 幻觉判定与 gold 不一致：{len(hallucination_mismatched)} 条 {hallucination_mismatched}")
    lines += [
        "- 逐条理由（judgment_reason / hallucination_reason）见 JSON 报告的 `cases` 字段。",
        "",
        "> 本报告不设任何质量百分比门槛：门槛要等人工确认这批一致性数据可信之后再定。",
        "",
    ]
    return "\n".join(lines)
