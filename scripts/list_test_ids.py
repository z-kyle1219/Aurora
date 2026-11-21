"""列出测试计划与测试实现之间的差异。

替代 TEST_PLAN 里手工维护的「是否已实现」状态列：
计划登记看 TEST_PLAN 的用例表首列，是否实现看 tests/ 下的函数名。

用法：uv run python scripts/list_test_ids.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs/testing/TEST_PLAN.md"
TESTS = ROOT / "tests"

# <模块>-<层级>-<序号>；层级段允许数字，否则 E2E 不会被匹配。
# 两段都用长度区间而不是层级白名单，新增层级（META、RISK 等）无需改脚本。
TEST_ID = re.compile(r"\b([A-Z]{2,4})-([A-Z0-9]{2,5})-(\d{3})\b")
# tests/ 中的函数名约定 test_<id 小写>__<behavior>()，双下划线是 Test ID 与行为的边界。
DEF_NAME = re.compile(r"def test_([a-z0-9]+)_([a-z0-9]{2,5})_(\d{3})__")


def plan_ids() -> set[str]:
    ids = set()
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        first = line.split("|")[1] if line.count("|") > 1 else ""
        m = TEST_ID.search(first)
        if m:
            ids.add("-".join(m.groups()))
    return ids


def impl_ids() -> set[str]:
    ids = set()
    for f in TESTS.rglob("test_*.py"):
        for m in DEF_NAME.finditer(f.read_text(encoding="utf-8")):
            ids.add(f"{m.group(1).upper()}-{m.group(2).upper()}-{m.group(3)}")
    return ids


def main() -> int:
    plan, impl = plan_ids(), impl_ids()
    missing, extra = sorted(plan - impl), sorted(impl - plan)

    print(f"计划登记 {len(plan)} 个 · 已实现 {len(impl)} 个")
    if missing:
        print(f"\n计划有、尚未实现（{len(missing)}）")
        for i in missing:
            print(f"  {i}")
    if extra:
        print(f"\n已实现、计划未登记（{len(extra)}）")
        for i in extra:
            print(f"  {i}")
    if not missing and not extra:
        print("\n计划与实现一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
