"""W3 tool use 演示 —— 电路分析 + 工具调用 + 校验回环。

用法（在 aurora 目录下）：
    uv run scripts/demo_tools.py

三个问题，正好覆盖 W3 的三个考点：
    1. 纯数值计算：验证「模型心算不可靠，数值交给工具算」；
    2. 反相比例运算电路（正常范围）：工具算完、校验通过、直接出答案；
    3. 反相比例运算电路（超电源电压）：触发「校验回环」，模型按警告修正。
"""

from aurora.agent import run_agent
from aurora.tools import validate_opamp_output

# 电源电压：运放输出不能超过它（校验回环用到的物理约束）
SUPPLY_VOLTAGE = 15.0

# 校验器：把「运放输出不能超电源」这条物理规则挂到 calculate_expression 上。
# 注意：校验器本应由 Router 按电路类型启用（只有运算电路才校验电压），
# 这里为了演示三个考点，手动给每道题指定是否启用。
OPAMP_VALIDATORS = {
    "calculate_expression": lambda result: validate_opamp_output(result, SUPPLY_VOLTAGE),
}

# (问题, 校验器) —— 第二项为 None 表示这道题不校验
QUESTIONS = [
    ("计算 3**4 + sqrt(2) * sqrt(8) 的值。", None),
    (
        "反相比例运算电路：R1 = 10kΩ，Rf = 100kΩ，输入电压 ui = -0.5V，"
        "电源电压 ±15V。求输出电压 uo。",
        OPAMP_VALIDATORS,
    ),
    (
        "反相比例运算电路：R1 = 10kΩ，Rf = 100kΩ，输入电压 ui = -3V，"
        "电源电压 ±15V。求输出电压 uo。",
        OPAMP_VALIDATORS,
    ),
]


def main() -> None:
    print("W3 tool use 演示：电路分析 + 工具调用 + 校验回环")
    print("=" * 70)

    for question, validators in QUESTIONS:
        print(f"\n[学生问题] {question}\n")

        answer, trace = run_agent(question, validators=validators)

        if trace:
            print("  [工具调用过程]")
            for step in trace:
                print(f"    - 调用 {step['tool']}({step['arguments']})")
                print(f"      结果: {step['result']}")
                if step["warning"]:
                    print(f"      [!] 校验警告: {step['warning']}")
        else:
            print("  （未调用工具，模型直接作答）")

        print(f"\n  [Aurora 回答]\n{answer}")
        print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
