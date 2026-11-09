"""公式求值：ast 白名单解析，不使用 eval。"""

from __future__ import annotations

import ast
import math
from typing import Any, Dict, Callable

import numpy as np
import pandas as pd


class ExprError(ValueError):
    """表达式不合法（语法错、用了不允许的语法、变量或函数不在白名单）。"""


def apply_scalar_or_series(fn: Callable, *args: Any) -> Any:
    """让白名单函数同时支持「标量入参」和「Series 入参」。"""
    series_args = [a for a in args if isinstance(a, pd.Series)]
    if not series_args:
        vals = [pd.Series([float(a)]) for a in args]
        return fn(*vals).iloc[0]
    idx = series_args[0].index
    norm: list = []
    for a in args:
        if isinstance(a, pd.Series):
            norm.append(pd.to_numeric(a, errors="coerce"))
        else:
            norm.append(pd.Series([float(a)] * len(idx), index=idx))
    return fn(*norm)


def safe_div(a: Any, b: Any) -> Any:
    """除法，但 0 分母返回 NaN 而不是报错/inf。指标计算里几乎总应该用这个。"""
    return apply_scalar_or_series(lambda x, y: pd.to_numeric(x, errors="coerce")
                  / pd.to_numeric(y, errors="coerce").replace(0, np.nan), a, b)


def _zscore(x: Any) -> Any:
    """标准化；标准差为 0（取值全同）时返回全 0，避免除零。"""
    return apply_scalar_or_series(lambda s: (lambda t: t * 0.0 if (sd := t.std(ddof=0)) == 0 or np.isnan(sd)
                             else (t - t.mean()) / sd)(pd.to_numeric(s, errors="coerce")), x)


def _max_ratio(x: Any) -> Any:
    """归一化到「占最大值的百分比」(0~100)。"""
    return apply_scalar_or_series(lambda s: (lambda t: t * 0.0 if (m := t.max()) == 0 or np.isnan(m)
                             else t / m * 100.0)(pd.to_numeric(s, errors="coerce")), x)


# 白名单函数表：YAML 公式里能写的函数全在这儿，改这个表就等于改口径能力
FUNCTIONS: Dict[str, Callable] = {
    "abs": lambda x: apply_scalar_or_series(lambda s: np.abs(pd.to_numeric(s, errors="coerce")), x),
    "sqrt": lambda x: apply_scalar_or_series(
        lambda s: np.sqrt(pd.to_numeric(s, errors="coerce").clip(lower=0)), x),
    "log": lambda x: apply_scalar_or_series(
        lambda s: np.log(pd.to_numeric(s, errors="coerce").clip(lower=1e-9)), x),
    "log1p": lambda x: apply_scalar_or_series(
        lambda s: np.log1p(pd.to_numeric(s, errors="coerce").clip(lower=0)), x),
    "exp": lambda x: apply_scalar_or_series(
        lambda s: np.exp(pd.to_numeric(s, errors="coerce").clip(upper=20)), x),
    "round": lambda x, n=0: apply_scalar_or_series(
        lambda s: pd.to_numeric(s, errors="coerce").round(int(n)), x),
    "min": lambda a, b: apply_scalar_or_series(
        lambda x, y: np.minimum(pd.to_numeric(x, errors="coerce"),
                                pd.to_numeric(y, errors="coerce")), a, b),
    "max": lambda a, b: apply_scalar_or_series(
        lambda x, y: np.maximum(pd.to_numeric(x, errors="coerce"),
                                pd.to_numeric(y, errors="coerce")), a, b),
    "clip": lambda x, lo, hi: apply_scalar_or_series(
        lambda s: pd.to_numeric(s, errors="coerce").clip(lower=lo, upper=hi), x),
    "safe_div": safe_div,
    "zscore": _zscore,
    "mean": lambda x: apply_scalar_or_series(lambda s: pd.to_numeric(s, errors="coerce").mean(), x),
    "median": lambda x: apply_scalar_or_series(lambda s: pd.to_numeric(s, errors="coerce").median(), x),
    "max_ratio": _max_ratio,
}

# 允许的二元运算符
_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: safe_div(a, b),
    ast.Pow: lambda a, b: pd.to_numeric(a, errors="coerce") ** b,
    ast.Mod: lambda a, b: pd.to_numeric(a, errors="coerce") % b,
}

_UNARY = {
    ast.USub: lambda a: -pd.to_numeric(a, errors="coerce"),
    ast.UAdd: lambda a: +pd.to_numeric(a, errors="coerce"),
}

# 允许的比较运算符（用于生成布尔掩码，如 attempts >= 3）
_CMPOPS = {
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


# 递归求值 AST 节点，白名单之外的语法一律拒绝
def _eval_node(node: ast.AST, variables: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)

    # 数字 / 字符串常量
    if isinstance(node, ast.Constant):
        return node.value

    # 变量引用：必须是白名单里的字段名
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        if node.id in FUNCTIONS:
            raise ExprError("函数 %s 必须被调用，不能单独引用" % node.id)
        raise ExprError("未知变量：%s（可用字段：%s）"
                        % (node.id, ", ".join(sorted(variables.keys()))))

    # 函数调用：必须在白名单
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExprError("只允许调用白名单函数，不允许属性调用")
        fname = node.func.id
        if fname not in FUNCTIONS:
            raise ExprError("不允许的函数：%s（白名单：%s）"
                            % (fname, ", ".join(sorted(FUNCTIONS.keys()))))
        if node.keywords:
            raise ExprError("不支持关键字参数：%s()" % fname)
        args = [_eval_node(a, variables) for a in node.args]
        try:
            return FUNCTIONS[fname](*args)
        except ExprError:
            raise
        except Exception as e:
            raise ExprError("函数 %s 执行失败：%s" % (fname, e))

    if isinstance(node, ast.BinOp):
        op = type(node.op)
        if op not in _BINOPS:
            raise ExprError("不允许的运算符：%s" % op.__name__)
        return _BINOPS[op](_eval_node(node.left, variables),
                           _eval_node(node.right, variables))

    if isinstance(node, ast.UnaryOp):
        op = type(node.op)
        if op not in _UNARY:
            raise ExprError("不允许的一元运算符：%s" % op.__name__)
        return _UNARY[op](_eval_node(node.operand, variables))

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comp in zip(node.ops, node.comparators):
            if type(op) not in _CMPOPS:
                raise ExprError("不允许的比较符")
            left = _CMPOPS[type(op)](left, _eval_node(comp, variables))
        return left

    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, variables) for v in node.values]
        if isinstance(node.op, ast.And):
            out = vals[0]
            for v in vals[1:]:
                out = out & v
            return out
        out = vals[0]
        for v in vals[1:]:
            out = out | v
        return out

    raise ExprError("不允许的语法：%s" % type(node).__name__)


def safe_eval(expr: str, variables: Dict[str, Any]) -> Any:
    """安全求值一个指标公式。"""
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError("公式为空")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError("公式语法错误：%s（%s）" % (expr, e))
    return _eval_node(tree, variables)


def check_formula(expr: str, available: Dict[str, Any]) -> None:
    """只做语法与引用校验，不求值。用于加载 YAML 时提前发现错误配置。"""
    safe_eval(expr, {k: pd.Series(dtype=float) for k in available})
