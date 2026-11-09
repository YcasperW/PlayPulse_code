"""版本对比：按关卡对齐两版数据，输出差异与结论。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import statsx
from .io_load import Dataset
from .semantics import Ontology
from .semantics.compute import player_level_matrix, compute_composites, compute_level_metrics


# 默认参与对比的指标（按「对策划决策最有信息量」排序）
DEFAULT_DIFF_METRICS = [
    "duration_sec", "stuck_ratio", "attempts",
    "time_dilation", "frustration", "quit_share",
]


@dataclass
class VersionDiff:
    """两个版本的对比结果。"""
    ver_a: str
    ver_b: str
    table: pd.DataFrame                 # 逐关逐指标明细
    overall: pd.DataFrame               # 不分关卡的整体对比
    n_a: int = 0
    n_b: int = 0
    harder_levels: List[int] = field(default_factory=list)
    easier_levels: List[int] = field(default_factory=list)
    conclusions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    difficulty_curve: Dict[str, List[float]] = field(default_factory=dict)

    def verdict_sentence(self) -> str:
        """一句话总评，直接放报告开头。"""
        if not self.conclusions:
            return "两个版本之间没有检测到可靠差异"
        return self.conclusions[0]


# 按指标配置取中位数或均值作为聚合函数
_agg = lambda m: np.median if m.agg == "median" else np.mean
# 数字格式化，非数字或 NaN 显示破折号
fmt_num = lambda x: "%.2f" % x if isinstance(x, (int, float, np.floating)) and not np.isnan(x) else "—"


def _diff_row(m, r: dict, ver_a: str, ver_b: str, level=None) -> dict:
    """把一次 compare 结果摊平成表行。level 为空表示不分关卡的整体对比。"""
    worse = (r["diff"] > 0) if m.direction == "higher_is_worse" else (r["diff"] < 0)
    row = {"指标": m.name, "指标id": m.id,
           "旧值(%s)" % ver_a: r["point_b"], "新值(%s)" % ver_b: r["point_a"],
           "变化": r["diff"],
           "变化率": (r["diff"] / r["point_b"]) if r["point_b"] else float("nan"),
           "差值区间下界": r["ci_low"], "差值区间上界": r["ci_high"],
           "效应量d": r["cohens_d"], "p值": r["p_value"],
           "超出噪声": "是" if r["beyond_noise"] else "否",
           "方向": "变差" if worse else "变好",
           "样本(旧/新)": "%d/%d" % (r["n_b"], r["n_a"]),
           "可信度": r["verdict_label"]}
    return dict(关卡=int(level), **row) if level is not None else row


def version_diff(ds: Dataset, onto: Ontology, ver_a: str, ver_b: str,
                 metrics: Optional[List[str]] = None) -> VersionDiff:
    """比较两个版本。ver_a 是基线（旧），ver_b 是对照（新）。"""
    versions = ds.versions
    if str(ver_a) not in versions or str(ver_b) not in versions:
        raise ValueError("数据中找不到指定版本。可用版本：%s" % versions)

    dsa = ds.subset_by_version(ver_a)
    dsb = ds.subset_by_version(ver_b)

    metrics = metrics or [m for m in DEFAULT_DIFF_METRICS if m in onto.metrics]

    # 每个指标的 (玩家 × 关卡) 矩阵只算一次，逐关和整体对比共用
    mats: Dict[str, tuple] = {}
    for mid in metrics:
        m = onto.metrics.get(mid)
        if m is None:
            continue
        try:
            mats[mid] = (m, player_level_matrix(dsa, onto, mid),
                         player_level_matrix(dsb, onto, mid))
        except Exception:
            continue

    rows: List[dict] = []
    for mid, (m, ma, mb) in mats.items():
        levels = sorted(set(ma.columns) | set(mb.columns))
        agg = _agg(m)

        for lv in levels:
            a = ma[lv].dropna().tolist() if lv in ma.columns else []
            b = mb[lv].dropna().tolist() if lv in mb.columns else []
            if len(a) < 2 or len(b) < 2:
                continue
            r = statsx.compare_two_groups(b, a, min_n=m.min_n, stat=agg)   # 新 vs 旧
            rows.append(_diff_row(m, r, ver_a, ver_b, level=lv))

    table = pd.DataFrame(rows)

    # 整体对比（不分关卡）
    overall_rows: List[dict] = []
    for mid, (m, ma, mb) in mats.items():
        a = ma.values.flatten()
        b = mb.values.flatten()
        a = a[~np.isnan(a)]
        b = b[~np.isnan(b)]
        if len(a) < 3 or len(b) < 3:
            continue
        r = statsx.compare_two_groups(b, a, min_n=m.min_n, stat=_agg(m))
        overall_rows.append(_diff_row(m, r, ver_a, ver_b))
    overall = pd.DataFrame(overall_rows)

    # 汇总结论
    harder: List[int] = []
    easier: List[int] = []
    if not table.empty and "difficulty_index" in [r["指标id"] for r in rows]:
        # 用难度指数定关卡的"变难/变易"
        di = table[table["指标id"] == "difficulty_index"]
        for _, r in di.iterrows():
            if r["超出噪声"] != "是":
                continue
            (harder if r["变化"] > 0 else easier).append(int(r["关卡"]))
    if not harder and not easier and not table.empty:
        # 没有难度指数时用耗时兜底
        du = table[table["指标id"] == "duration_sec"]
        for _, r in du.iterrows():
            if r["超出噪声"] != "是":
                continue
            (harder if r["变化"] > 0 else easier).append(int(r["关卡"]))

    conclusions = _conclusion_lines(ver_a, ver_b, table, overall, harder, easier)

    warnings: List[str] = []
    if dsa.n_players < dsb.n_players * 0.5 or dsb.n_players < dsa.n_players * 0.5:
        warnings.append("两个版本的玩家数相差较大（%d vs %d），对比结果受样本不平衡影响"
                        % (dsa.n_players, dsb.n_players))
    min_n = min(dsa.n_players, dsb.n_players)
    if min_n < 8:
        warnings.append("样本量偏小（每组最少 %d 人）。**本对比只能作为方向参考，"
                        "不足以支撑直接调数值**" % min_n)
    same_players = set(dsa.df["player_id"].astype(str)) & set(dsb.df["player_id"].astype(str))
    if same_players:
        warnings.append("有 %d 位玩家同时出现在两个版本中（组内对比）。"
                        "同一批人玩两次会产生学习效应，**新版本通常会显得更简单**"
                        % len(same_players))

    # 难度曲线对比数据（画图用）
    curve: Dict[str, List[float]] = {}
    for tag, sub in ((str(ver_a), dsa), (str(ver_b), dsb)):
        lm = compute_level_metrics(sub, onto)
        lm = compute_composites(lm, onto)
        if "difficulty_index" in lm.wide.columns:
            curve[tag] = [float(x) for x in lm.wide["difficulty_index"].tolist()]
        elif "duration_sec" in lm.wide.columns:
            curve[tag] = [float(x) for x in lm.wide["duration_sec"].tolist()]

    return VersionDiff(ver_a=str(ver_a), ver_b=str(ver_b), table=table, overall=overall,
                       n_a=dsa.n_players, n_b=dsb.n_players,
                       harder_levels=sorted(set(harder)), easier_levels=sorted(set(easier)),
                       conclusions=conclusions, warnings=warnings, difficulty_curve=curve)


def _conclusion_lines(ver_a: str, ver_b: str, table: pd.DataFrame,
                       overall: pd.DataFrame, harder: List[int], easier: List[int]) -> List[str]:
    """生成人话结论。**第一句是总评**，后面是支撑细节。"""
    out: List[str] = []

    if table.empty:
        return ["两个版本之间没有可比较的指标（数据字段不足）"]

    reliable = table[table["超出噪声"] == "是"]
    if reliable.empty:
        out.append("**未检测到超出噪声的差异**：%s 与 %s 的各项指标差异都落在随机波动范围内，"
                   "不能说新版本真的变好或变差了。建议补样本后再判断" % (ver_a, ver_b))
    else:
        n_hard, n_easy = len(harder), len(easier)
        if n_hard and n_easy:
            out.append("**有得有失**：%s 相比 %s，第 %s 关变难、第 %s 关变易"
                       % (ver_b, ver_a, "、".join(map(str, harder)), "、".join(map(str, easier))))
        elif n_hard:
            out.append("**整体变难**：%s 相比 %s，第 %s 关难度显著上升，其余关卡变化未超出噪声"
                       % (ver_b, ver_a, "、".join(map(str, harder))))
        elif n_easy:
            out.append("**整体变易**：%s 相比 %s，第 %s 关难度显著下降，其余关卡变化未超出噪声"
                       % (ver_b, ver_a, "、".join(map(str, easier))))
        else:
            out.append("部分指标出现超出噪声的变化，但难度指数未发生可靠改变")

    # 最显著的单条变化
    if not overall.empty:
        sig = overall[overall["超出噪声"] == "是"].copy()
        if not sig.empty:
            sig["absd"] = sig["效应量d"].abs()
            top = sig.sort_values("absd", ascending=False).iloc[0]
            out.append("变化最大的指标是「%s」：%s → %s（d=%.2f，%s）"
                       % (top["指标"], fmt_num(top["旧值(%s)" % ver_a]),
                          fmt_num(top["新值(%s)" % ver_b]),
                          float(top["效应量d"]), statsx.explain_effect_size(float(top["效应量d"]))))

    # 准流失信号的变化
    q = table[table["指标id"] == "quit_share"]
    if not q.empty:
        qs = q[q["超出噪声"] == "是"]
        for _, r in qs.iterrows():
            if r["变化"] > 0:
                out.append("⚠️ 第 %d 关「想退出」占比上升（%.0f%% → %.0f%%）——**新版本在这里更赶人了**"
                           % (int(r["关卡"]), r["旧值(%s)" % ver_a] * 100,
                              r["新值(%s)" % ver_b] * 100))
            elif r["变化"] < 0:
                out.append("第 %d 关「想退出」占比下降（%.0f%% → %.0f%%），流失风险缓解"
                           % (int(r["关卡"]), r["旧值(%s)" % ver_a] * 100,
                              r["新值(%s)" % ver_b] * 100))

    # 噪声提示
    noisy = table[table["超出噪声"] != "是"]
    if len(noisy) > len(reliable) and len(reliable) > 0:
        out.append("另有 %d 项变化未超出噪声区间，**不要把它们当成真实改善汇报**"
                   % len(noisy))

    return out
