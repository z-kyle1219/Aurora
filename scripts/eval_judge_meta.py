"""EVL-META-001 在线入口 —— 裁判一致性元评估（付费作业，不属于普通回归）。

用途：让 DeepSeek 裁判去判 40 条带双标标注的样本，输出 agreement、Cohen's κ、
每类 precision/recall，并把同源模型偏差单列。它回答的不是「助教答得好不好」，
而是「后面那些质量指标值不值得信」。

前提：只有数据集 `annotation.status` 为 `human_double_annotated`（且标注者是两
位不同的人）时，这些数字才是「裁判与人工的一致性」。当前数据集是 AI 起草的
标注草稿，脚本默认拒绝执行；`--allow-draft-labels` 只用于验证链路，报告与终端
都会标明「草稿 gold、不可引用」。

用法（在 aurora 目录下）：

    # 1. 预检：只校验数据集格式，不联网、不需要凭据、不读 .env
    uv run python scripts/eval_judge_meta.py --validate-only

    # 2. 在线运行：凭据只能来自进程环境，脚本不读 .env
    DEEPSEEK_API_KEY=sk-... uv run python scripts/eval_judge_meta.py

    # 3. 异源裁判对照：换模型/网关再跑一次，同源偏差会自动分成两组
    DEEPSEEK_API_KEY=sk-... uv run python scripts/eval_judge_meta.py \
        --model gpt-4o-mini --base-url https://api.example.com/v1 --key-env OPENAI_API_KEY

    # 4. 留档：token 与费用（单价按官方当期价格传入，不写死在代码里）
    ... --out data/eval/meta/reports/judge_meta_2026-11-22.json \
        --price-input-per-m 0.28 --price-output-per-m 0.42

费用：40 条 × 2 次调用 = 80 次请求，全部 temperature=0.0。样本量固定，
不会因为重跑而放大费用；`--limit` 可先跑前 N 条做连通性验证。

边界：不设质量百分比门槛；报告只出数字与需人工复核的样本清单。
"""

import argparse
import json
import os
import sys
from pathlib import Path

AURORA_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = AURORA_ROOT / "data" / "eval" / "meta" / "judge_agreement_v1.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EVL-META-001：裁判与双标标注的一致性（付费在线作业；草稿标注默认拒绝）",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="元评估数据集路径")
    parser.add_argument("--validate-only", action="store_true", help="只校验数据集，不联网、不需要凭据")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条，用于连通性验证")
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                        help="裁判模型（默认取 DEEPSEEK_MODEL 或 deepseek-chat）")
    parser.add_argument("--base-url",
                        default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                        help="OpenAI 兼容网关地址")
    parser.add_argument("--key-env", default="DEEPSEEK_API_KEY",
                        help="存放 API key 的环境变量名；只读进程环境，不读 .env")
    parser.add_argument("--allow-draft-labels", action="store_true",
                        help="标注状态仍为 AI 草稿时仍执行；报告会带不可引用的声明")
    parser.add_argument("--out", type=Path, default=None, help="JSON 报告输出路径（可选）")
    parser.add_argument("--price-input-per-m", type=float, default=None,
                        help="输入单价（每百万 token，按官方当期价格填写）")
    parser.add_argument("--price-output-per-m", type=float, default=None,
                        help="输出单价（每百万 token，按官方当期价格填写）")
    return parser.parse_args(argv)


def _block_dotenv() -> None:
    """在导入 aurora 业务模块前屏蔽 dotenv。

    `aurora.config` 在导入时调用 `load_dotenv()`；脚本要保证「凭据只能来自
    进程环境」，因此先把这个入口换成空实现。凭据检查也发生在导入之前，
    这样 .env 里即使有 key 也无法满足前置条件。
    """
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: ARG005


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # judge_meta 不导入 aurora.config，因此这一步完全不触碰 .env。
    from aurora import judge_meta

    try:
        dataset = judge_meta.load_dataset(args.dataset)
    except ValueError as exc:
        print(f"数据集校验失败：{exc}", file=sys.stderr)
        return 2

    cases = dataset.cases
    summary = judge_meta.dataset_summary(dataset)
    print(f"数据集：{summary['path']}")
    print(f"版本：{summary['version']}  条数：{summary['cases']}  分层：{summary['by_category']}")
    print(f"标注状态：{summary['annotation_status']}  标注者：{summary['annotators']}")
    print(f"不一致并仲裁：{summary['adjudicated']} 条")

    if args.validate_only:
        print("校验通过；未联网、未读取 .env、未产生调用。")
        return 0

    if not dataset.is_human_double_annotated and not args.allow_draft_labels:
        print(
            "错误：数据集标注状态为 "
            f"{summary['annotation_status']}，两版标签都是 AI 草稿，没有两位标注者独立标注。\n"
            "此时跑出的 agreement 不能当作裁判可信度。请先完成人工双标并把 annotation.status "
            "改为 human_double_annotated；若只想验证脚本链路，加 --allow-draft-labels。",
            file=sys.stderr,
        )
        return 3
    if not dataset.is_human_double_annotated:
        print("警告：标注仍为 AI 草稿，本报告不得作为裁判可信度或质量门槛的依据。")

    if args.limit is not None:
        if args.limit <= 0:
            print("--limit 必须为正整数", file=sys.stderr)
            return 2
        cases = cases[: args.limit]
        print(f"按 --limit 只评测前 {len(cases)} 条")

    api_key = os.environ.get(args.key_env)
    if not api_key:
        print(
            f"错误：需要显式提供环境变量 {args.key_env}。\n"
            "本脚本不读取 .env，也不回退到本机密钥；请用 "
            f"`{args.key_env}=... uv run python scripts/eval_judge_meta.py` 运行。",
            file=sys.stderr,
        )
        return 2

    _block_dotenv()
    from openai import OpenAI

    # judge_meta.make_judge_functions 在此之后才导入 aurora.judge，
    # 因此 config 的 load_dotenv 已被屏蔽，.env 不会参与凭据或地址解析。
    client = judge_meta.UsageTrackingClient(OpenAI(api_key=api_key, base_url=args.base_url))
    prices = None
    if args.price_input_per_m is not None and args.price_output_per_m is not None:
        prices = {
            "input_per_million": args.price_input_per_m,
            "output_per_million": args.price_output_per_m,
            "currency": "USD",
        }

    print(f"裁判模型：{args.model} @ {args.base_url}；共 {2 * len(cases)} 次请求…")
    report = judge_meta.evaluate_with_client(
        cases,
        client=client,
        judge_model=args.model,
        dataset={**summary, "evaluated": len(cases)},
        base_url=args.base_url,
        prices=prices,
    )
    markdown = judge_meta.format_report(report)
    print("\n" + markdown)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON 报告已写入：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
