"""W3 工具层 —— 把「本地 Python 函数」封装成模型可调用的 tool。

这是 W3「tool use」的第一块拼图，对应两条链路里的「路径① 内部函数」：
把函数封装成 tool（名称 + 描述 + 参数 schema），交给模型去「点菜」。

本文件只做三件事，全部不碰 LLM（纯函数，方便单独测试）：
    1. calculate_expression：真正执行计算（ast 白名单，安全求值）
    2. TOOLS：OpenAI 兼容的工具定义（模型看到的「菜单」）
    3. execute_tool：agent loop 调用工具的统一入口（按名称分发）

安全考点（面试高频）：
模型输出的表达式属于「半可信输入」，绝不能直接 eval()——它可能包含
任意 Python 代码。所以这里用手写 ast 白名单：只放行数字、四则运算、
幂运算和几个数学函数，其它语法一律拒绝。
"""

import ast
import math
import operator

# ---------------------------------------------------------------------------
# 1. 安全求值：只允许白名单语法，拒绝一切危险的表达式
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}

_ALLOWED_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "pow": pow,
}


def _eval_node(node: ast.AST) -> float:
    """递归求值一个 AST 节点。只有进了白名单的节点类型才会走到这里。"""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
        return _ALLOWED_NAMES[node.id]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _ALLOWED_FUNCS
    ):
        return _ALLOWED_FUNCS[node.func.id](*[_eval_node(a) for a in node.args])
    raise ValueError(f"表达式包含不允许的语法：{ast.dump(node)[:60]}…")


def calculate_expression(expr: str) -> float:
    """用白名单安全地计算一个数学表达式，返回数值。

    例：calculate_expression("-(100/10)*(-3)") -> 30.0
    """
    tree = ast.parse(expr, mode="eval")
    return float(_eval_node(tree))


# ---------------------------------------------------------------------------
# 2. 工具定义：模型看到的「菜单」（OpenAI 兼容的 function schema）
# ---------------------------------------------------------------------------

CALCULATOR_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_expression",
        "description": (
            "用 Python 精确计算一个数学表达式，返回数值结果。"
            "任何需要算术计算的环节都必须调用本工具，不要心算。"
            "支持 + - * / ** 以及 sqrt、sin、cos、log、exp 等函数。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expr": {
                    "type": "string",
                    "description": "要计算的数学表达式，例如 '-(100/10)*(-0.5)'",
                }
            },
            "required": ["expr"],
        },
    },
}

# 模型侧的「菜单」：以后要加新工具，往这个列表里追加即可
TOOLS = [CALCULATOR_TOOL]


# ---------------------------------------------------------------------------
# 3. 执行入口：按工具名分发，返回给模型看的字符串结果
# ---------------------------------------------------------------------------

def execute_tool(name: str, arguments: dict) -> str:
    """agent loop 调用工具的统一切入点：识别函数名和参数 → 执行 → 返回结果字符串。

    现在只有一个工具；以后要加「查教材」「画波形」等工具时，在这里加分支即可。
    模型侧的「菜单」（TOOLS）和执行侧的「后厨」（本函数）一一对应。
    """
    if name != "calculate_expression":
        return f"错误：未知工具 {name}"

    expr = arguments.get("expr") if isinstance(arguments, dict) else None
    if not isinstance(expr, str) or not expr.strip():
        return "错误：缺少字符串参数 expr"

    try:
        value = calculate_expression(expr)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        return f"计算失败：{exc}"
    return f"{value:g}"


# ---------------------------------------------------------------------------
# 4. 合理性校验：结果要符合物理约束（W3 的「校验回环」）
# ---------------------------------------------------------------------------

def validate_opamp_output(result: str, supply_voltage: float) -> str | None:
    """校验运放输出电压是否超出电源电压。

    这是「校验回环」的一个具体规则：算出来的输出电压绝对值不能超过
    电源电压，否则说明运放已饱和，实际输出会被钳位在 ±电源电压。

    返回 None 表示通过校验；返回警告字符串表示需要修正。
    """
    try:
        value = float(result)
    except ValueError:
        return None  # 结果不是数值（比如是错误信息），跳过校验
    if abs(value) > supply_voltage:
        sign = "+" if value > 0 else "-"
        return (
            f"算出的 {value:g}V 已超出运放电源电压 ±{supply_voltage:g}V，"
            f"说明运放饱和，实际输出会被钳位在 {sign}{supply_voltage:g}V。"
            "请基于钳位后的值修正结论。"
        )
    return None
