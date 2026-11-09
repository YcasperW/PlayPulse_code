"""统计底座：Bootstrap 区间、效应量与样本量判定。"""

from __future__ import annotations

import math
from typing import Callable, Dict, Sequence

import numpy as np


VERDICT_CONCLUSION = "conclusion"        # 可以给结论
VERDICT_DIRECTION = "direction_only"     # 只给方向
VERDICT_INSUFFICIENT = "insufficient"    # 拒绝给任何判断

VERDICT_LABEL = {
    VERDICT_CONCLUSION: "可下结论",
    VERDICT_DIRECTION: "仅供方向参考",
    VERDICT_INSUFFICIENT: "样本不足，不给出判断",
}

N_BOOT = 4000
BOOT_SEED = 20260922
_ALPHA_LO = 2.5      # 95% 区间的下分位
_ALPHA_HI = 97.5


# 数字格式化，空值显示破折号
def fmt_num(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if abs(x) >= 100:
        return "%.0f" % x
    return "%.2f" % x


def verdict_by_sample_size(n: int, min_n: int) -> str:
    """根据玩家数判定能给到什么程度的结论。"""
    if n >= min_n:
        return VERDICT_CONCLUSION
    if n >= max(2, math.ceil(min_n / 2)):
        return VERDICT_DIRECTION
    return VERDICT_INSUFFICIENT


def describe_sample(values: Sequence[float], min_n: int = 8,
             stat: Callable[[np.ndarray], float] = np.median) -> Dict:
    """一个样本的诚实描述：点估计 + Bootstrap 置信区间 + 诚实性判定。"""
    x = np.asarray(values, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0:
        return {"n": 0, "point": float("nan"), "ci_low": float("nan"),
                "ci_high": float("nan"), "spread": float("nan"),
                "verdict": VERDICT_INSUFFICIENT,
                "verdict_label": VERDICT_LABEL[VERDICT_INSUFFICIENT],
                "note": "无数据", "min": float("nan"), "max": float("nan")}

    point = float(stat(x))

    # Bootstrap 百分位区间。不用 BCa：它更准但实现复杂，小样本上反而不稳定；
    # percentile 直观、可解释、可复现，对这个场景足够
    lo = hi = float("nan")
    if n >= 3:
        rng = np.random.default_rng(BOOT_SEED)
        stats = np.apply_along_axis(stat, 1, x[rng.integers(0, n, size=(N_BOOT, n))])
        stats = stats[~np.isnan(stats)]
        if len(stats):
            lo = float(np.percentile(stats, _ALPHA_LO))
            hi = float(np.percentile(stats, _ALPHA_HI))

    v = verdict_by_sample_size(n, min_n)
    if v == VERDICT_CONCLUSION:
        note = "样本 %d 人（门槛 %d），结论可信度可接受" % (n, min_n)
    elif v == VERDICT_DIRECTION:
        note = ("样本仅 %d 人，低于门槛 %d：**本条只代表方向，不代表结论**，"
                "不要据此直接改数值" % (n, min_n))
    else:
        note = ("样本仅 %d 人，远低于门槛 %d：**本模块拒绝给出判断**。"
                "请先补样本，或把这条当作个案观察" % (n, min_n))

    return {
        "n": n,
        "point": point,
        "ci_low": lo,
        "ci_high": hi,
        "spread": (hi - lo) if not (math.isnan(lo) or math.isnan(hi)) else float("nan"),
        "verdict": v,
        "verdict_label": VERDICT_LABEL[v],
        "note": note,
        "min": float(np.min(x)),
        "max": float(np.max(x)),
    }


def compare_two_groups(a: Sequence[float], b: Sequence[float], min_n: int = 8,
            stat: Callable[[np.ndarray], float] = np.median) -> Dict:
    """两组完整对比：点估计差、效应量、差值区间、p 值、以及一句诚实结论。"""
    a = np.asarray(a, dtype=float)
    a = a[~np.isnan(a)]
    b = np.asarray(b, dtype=float)
    b = b[~np.isnan(b)]
    na, nb = len(a), len(b)

    pa = float(stat(a)) if na else float("nan")
    pb = float(stat(b)) if nb else float("nan")
    diff = pa - pb if na and nb else float("nan")

    # Cohen's d 量化差异幅度，p 值只回答是否可能由随机造成。
    # p 值对样本量敏感，故同时给出幅度
    d = float("nan")
    if na >= 2 and nb >= 2:
        pooled = ((na - 1) * np.var(a, ddof=1) + (nb - 1) * np.var(b, ddof=1)) / (na + nb - 2)
        d = 0.0 if pooled <= 0 else float((np.mean(a) - np.mean(b)) / math.sqrt(pooled))

    # 两组统计量之差的 Bootstrap 区间（a - b）。跨过 0 就意味着"可能只是噪声"
    lo = hi = float("nan")
    if na >= 3 and nb >= 3:
        rng = np.random.default_rng(BOOT_SEED)
        diffs = np.empty(N_BOOT)
        for i in range(N_BOOT):
            diffs[i] = stat(a[rng.integers(0, na, na)]) - stat(b[rng.integers(0, nb, nb)])
        diffs = diffs[~np.isnan(diffs)]
        if len(diffs):
            lo = float(np.percentile(diffs, _ALPHA_LO))
            hi = float(np.percentile(diffs, _ALPHA_HI))

    # Mann-Whitney U：不假设正态，对右偏的时长数据比 t 检验合适。
    # 注意即使 p 很小也要结合效应量看——小样本下 p 值本身不稳定
    p = float("nan")
    if na >= 3 and nb >= 3:
        try:
            from scipy.stats import mannwhitneyu
            _, p = mannwhitneyu(a, b, alternative="two-sided")
            p = float(p)
        except Exception:
            pass

    beyond = False
    if not (math.isnan(lo) or math.isnan(hi)):
        beyond = (lo > 0) or (hi < 0)

    n_min = min(na, nb)
    v = verdict_by_sample_size(n_min, min_n)

    if math.isnan(diff):
        conclusion = "样本不足，无法比较"
    elif beyond and v == VERDICT_CONCLUSION:
        conclusion = "差异超出噪声：由 %s 变为 %s，%s" % (
            fmt_num(pb), fmt_num(pa), explain_effect_size(d))
    elif beyond:
        conclusion = "看起来有变化（%s → %s），但样本仅 %d 人，只作方向参考" % (
            fmt_num(pb), fmt_num(pa), n_min)
    else:
        conclusion = "差异未超出噪声区间，无法确认真的变了（%s → %s，差值区间 %.2f ~ %.2f）" % (
            fmt_num(pb), fmt_num(pa), lo, hi)

    return {
        "n_a": na, "n_b": nb,
        "point_a": pa, "point_b": pb, "diff": diff,
        "ci_low": lo, "ci_high": hi,
        "cohens_d": d, "p_value": p,
        "beyond_noise": beyond,
        "verdict": v, "verdict_label": VERDICT_LABEL[v],
        "conclusion": conclusion,
    }


def explain_effect_size(d: float) -> str:
    """把效应量翻译成人话（|d|<0.2 几乎无差异 / 0.2-0.5 小 / 0.5-0.8 中 / >0.8 大）。"""
    if d is None or (isinstance(d, float) and math.isnan(d)):
        return "无法计算"
    a = abs(d)
    if a < 0.2:
        level = "几乎无差异"
    elif a < 0.5:
        level = "小差异"
    elif a < 0.8:
        level = "中等差异"
    else:
        level = "大差异"
    direction = "变高/变难" if d > 0 else "变低/变易"
    return "%s（%s，d=%.2f）" % (level, direction, d)
