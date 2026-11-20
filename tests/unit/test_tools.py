"""本地计算工具纯逻辑测试。"""

import math
import os

import pytest

from aurora.tools import calculate_expression, execute_tool, validate_opamp_output


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("-(100/10)*(-3)", 30.0),
        ("3**4+sqrt(2)*sqrt(8)", 85.0),
        ("pi", math.pi),
        ("e", math.e),
        ("-2", -2.0),
        ("7%3", 1.0),
    ],
    ids=["nested-signs", "power-and-sqrt", "pi", "e", "negative", "modulo"],
)
def test_tol_unit_001__calculates_whitelisted_expressions(expression, expected):
    """TOL-UNIT-001：白名单算术和常量返回可手算的 float。"""
    result = calculate_expression(expression)

    assert isinstance(result, float)
    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    ("expression", "expected_exception", "message_pattern"),
    [
        ('__import__("os").system("echo unsafe")', ValueError, "不允许的语法"),
        ("(1).__class__", ValueError, "不允许的语法"),
        ("[1,2][0]", ValueError, "不允许的语法"),
        ("lambda:1", ValueError, "不允许的语法"),
        ("a=1", SyntaxError, "invalid syntax"),
        ('"a" + "b"', ValueError, "不允许的语法"),
    ],
    ids=["import", "attribute", "subscript", "lambda", "assignment", "string-concatenation"],
)
def test_tol_unit_002__rejects_dangerous_or_non_numeric_syntax(
    monkeypatch, expression, expected_exception, message_pattern
):
    """TOL-UNIT-002：区分语法解析失败与 AST 白名单拒绝，且无副作用。"""
    system_calls = []
    monkeypatch.setattr(os, "system", lambda command: system_calls.append(command))

    with pytest.raises(expected_exception, match=message_pattern):
        calculate_expression(expression)

    assert system_calls == []


def test_tol_unit_003__keyword_arguments_are_rejected_explicitly():
    """TOL-UNIT-003 / DEF-01：关键字参数不再被静默丢弃，而是显式拒绝。"""
    result = execute_tool("calculate_expression", {"expr": "round(3.14159, ndigits=2)"})

    assert result.startswith("计算失败：")
    assert "关键字参数" in result
    assert result != "3"


def test_tol_unit_003__positional_round_keeps_precision():
    """TOL-UNIT-003：拒绝关键字参数不影响位置参数写法（round(x, 2) 仍为 3.14）。"""
    assert execute_tool("calculate_expression", {"expr": "round(3.14159, 2)"}) == "3.14"


@pytest.mark.parametrize(
    "expression",
    ["1/0", "log(0)", "sqrt(-1)", "10**10000"],
    ids=["division-by-zero", "log-domain", "sqrt-domain", "float-overflow"],
)
def test_tol_unit_005__converts_supported_calculation_errors(expression):
    """TOL-UNIT-005：已支持的数值异常转换为可识别错误字符串。"""
    result = execute_tool("calculate_expression", {"expr": expression})

    assert result.startswith("计算失败：")
    assert len(result) > len("计算失败：")


@pytest.mark.parametrize(
    "expression",
    ["(-8)**(1/3)", "1+", "sqrt()"],
    ids=["non-real-value-error", "syntax-error", "wrong-arity"],
)
def test_tol_unit_006__converts_expected_expression_errors(expression):
    """TOL-UNIT-006 / DEF-02：非实数、语法错误与参数个数错误都转换为错误字符串。"""
    result = execute_tool("calculate_expression", {"expr": expression})

    assert result.startswith("计算失败：")


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("unknown", {"expr": "1+1"}, "错误：未知工具 unknown"),
        ("calculate_expression", None, "错误：缺少字符串参数 expr"),
        ("calculate_expression", [], "错误：缺少字符串参数 expr"),
        ("calculate_expression", {}, "错误：缺少字符串参数 expr"),
        ("calculate_expression", {"expr": ""}, "错误：缺少字符串参数 expr"),
        ("calculate_expression", {"expr": "   "}, "错误：缺少字符串参数 expr"),
        ("calculate_expression", {"expr": 2}, "错误：缺少字符串参数 expr"),
    ],
    ids=["unknown-tool", "none", "list", "missing", "empty", "blank", "non-string"],
)
def test_tol_unit_007__validates_dispatch_name_and_arguments(name, arguments, expected):
    """TOL-UNIT-007：未知工具和非法参数返回精确、可识别错误。"""
    assert execute_tool(name, arguments) == expected


@pytest.mark.parametrize(
    ("result", "supply", "warning_sign"),
    [
        ("15", 15.0, None),
        ("-15", 15.0, None),
        ("15.01", 15.0, "+15V"),
        ("-20", 15.0, "-15V"),
        ("错误：计算失败", 15.0, None),
        ("abc", 15.0, None),
        ("12.01", 12.0, "+12V"),
    ],
    ids=["positive-boundary", "negative-boundary", "positive-over", "negative-over", "error-text", "non-numeric", "custom-supply"],
)
def test_tol_unit_009__validates_opamp_boundary_sign_and_supply(result, supply, warning_sign):
    """TOL-UNIT-009：边界不过度告警，越界符号与自定义电源正确。"""
    warning = validate_opamp_output(result, supply)

    if warning_sign is None:
        assert warning is None
    else:
        assert warning is not None
        assert "运放饱和" in warning
        assert f"±{supply:g}V" in warning
        assert f"钳位在 {warning_sign}" in warning
