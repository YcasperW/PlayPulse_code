"""指标计算：行级公式、按玩家聚合、Bootstrap 与复合指标。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import re

import numpy as np
import pandas as pd

from . import expr
from .registry import Ontology, MetricDef, CompositeDef
from .. import statsx
from ..io_load import Dataset


AGG_FUNCS = {
    "median": np.median,
    "mean": np.mean,
    "max": np.max,
    "min": np.min,
}

# (客观偏高, 感知偏高) -> 象限名 -> YAML 里对应的建议键
_QUADRANT_NAME = {(True, True): "真实卡点", (False, True): "假性难度",
                  (False, False): "顺滑但可能无聊", (True, False): "心流/无感消耗"}
_QUADRANT_KEY = {"真实卡点": "high_obj_high_per", "假性难度": "low_obj_high_per",
                 "顺滑但可能无聊": "low_obj_low_per", "心流/无感消耗": "high_obj_low_per"}


@dataclass
class LevelMetrics:
    """关卡级指标计算结果。long 是长表（每关每指标一行，带区间），"""
    long: pd.DataFrame
    wide: pd.DataFrame
    n_players_total: int
    meta: Dict[str, dict] = field(default_factory=dict)
    skipped: List[Tuple[str, str]] = field(default_factory=list)

    def value_at(self, metric: str, level: int) -> float:
        """取某个指标在某关的点估计。"""
        row = self.long[(self.long["metric"] == metric) & (self.long["level_id"] == level)]
        if row.empty:
            return float("nan")
        return float(row["point"].iloc[0])

    # 取某指标在某关的点估计、置信区间、样本量与判定
    def detail_at(self, metric: str, level: int) -> dict:
        row = self.long[(self.long["metric"] == metric) & (self.long["level_id"] == level)]
        if row.empty:
            return {}
        r = row.iloc[0]
        return {"point": r["point"], "ci_low": r["ci_low"], "ci_high": r["ci_high"],
                "n": int(r["n"]), "verdict": r["verdict"], "verdict_label": r["verdict_label"]}


# 求值结果可能是标量（公式里没引用任何字段），统一铺成跟行数一样长的 Series
_as_series = lambda s, idx: (pd.to_numeric(s, errors="coerce") if isinstance(s, pd.Series)
                             else pd.Series([float(s)] * len(idx), index=idx))


# 行级值 → (玩家 × 关卡) 长表。后续统计只能基于它，不能基于原始行
def player_level_frame(df: pd.DataFrame, values: pd.Series) -> pd.DataFrame:
    t = pd.DataFrame({
        "player_id": df["player_id"].astype(str),
        "level_id": pd.to_numeric(df["level_id"], errors="coerce"),
        "_v": values,
    }).dropna(subset=["level_id"])
    t["level_id"] = t["level_id"].astype(int)
    return t


def eval_formula_rows(df: pd.DataFrame, formula: str) -> pd.Series:
    """在 DataFrame 上求值一个指标公式，返回行级 Series。"""
    # 从公式里提取字段名，函数名排除在外
    ids = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula or ""))
    fields = [i for i in ids if i not in expr.FUNCTIONS]
    variables = {name: (df[name] if name in df.columns
                        else pd.Series(np.nan, index=df.index)) for name in fields}
    return expr.safe_eval(formula, variables)


def compute_level_metrics(ds: Dataset, onto: Ontology,
                          metrics: Optional[List[str]] = None,
                          levels: Optional[List[int]] = None) -> LevelMetrics:
    """按指标本体计算所有（或指定）指标的关卡级统计。"""
    df = ds.df
    if df is None or df.empty:
        return LevelMetrics(long=pd.DataFrame(), wide=pd.DataFrame(), n_players_total=0)

    if "player_id" not in df.columns:
        raise ValueError("缺少 player_id，无法按玩家聚合（样本量单位必须是玩家）")

    levels = levels or ds.levels
    chosen = [onto.metrics[m] for m in (metrics or list(onto.metrics.keys()))
              if m in onto.metrics]

    records: List[dict] = []
    skipped: List[Tuple[str, str]] = []
    meta: Dict[str, dict] = {}

    for m in chosen:
        # 1. 字段够不够
        missing = [f for f in m.required_fields() if f not in df.columns
                   or df[f].dropna().empty]
        if missing:
            skipped.append((m.id, "缺字段：" + "、".join(missing)))
            continue

        # 2. 行级求值
        try:
            s = eval_formula_rows(df, m.formula)
        except expr.ExprError as e:
            skipped.append((m.id, "公式执行失败：%s" % e))
            continue

        tmp = player_level_frame(df, _as_series(s, df.index))

        # 3. (玩家 × 关卡) 聚合
        agg_fn = AGG_FUNCS.get(m.agg, np.median)
        pl = (tmp.groupby(["level_id", "player_id"])["_v"]
                 .agg(agg_fn)
                 .reset_index())

        # 4. 关卡级统计：对"每位玩家一个值"做 Bootstrap
        for lv in levels:
            sub = pl[pl["level_id"] == lv]["_v"].dropna()
            if sub.empty:
                continue
            d = statsx.describe_sample(sub.tolist(), min_n=m.min_n,
                                stat=AGG_FUNCS.get(m.agg, np.median))
            records.append({
                "metric": m.id,
                "metric_name": m.name,
                "level_id": lv,
                "point": d["point"],
                "ci_low": d["ci_low"],
                "ci_high": d["ci_high"],
                "n": d["n"],
                "verdict": d["verdict"],
                "verdict_label": d["verdict_label"],
                "min": d.get("min", float("nan")),
                "max": d.get("max", float("nan")),
            })

        meta[m.id] = {"name": m.name, "unit": m.unit, "layer": m.layer,
                      "direction": m.direction, "why": m.why, "caveat": m.caveat,
                      "fix": m.fix, "min_n": m.min_n,
                      "warn_above": m.warn_above, "warn_below": m.warn_below,
                      "formula": m.formula, "agg": m.agg}

    long = pd.DataFrame(records)
    if long.empty:
        return LevelMetrics(long=long, wide=pd.DataFrame(),
                            n_players_total=ds.n_players, meta=meta, skipped=skipped)

    wide = long.pivot(index="level_id", columns="metric", values="point")
    wide = wide.reindex(sorted(wide.index))

    return LevelMetrics(long=long, wide=wide, n_players_total=ds.n_players,
                        meta=meta, skipped=skipped)


def compute_composites(lm: LevelMetrics, onto: Ontology) -> LevelMetrics:
    """在已有基础指标上算复合指标（难度指数 / 感知难度曲线），并合并回 lm。"""
    if lm.wide is None or lm.wide.empty:
        return lm

    extra_records: List[dict] = []

    for cid, c in onto.composites.items():
        cols = [x.metric for x in c.components if x.metric in lm.wide.columns]
        if not cols:
            continue
        total_w = sum(x.weight for x in c.components if x.metric in lm.wide.columns)
        if total_w <= 0:
            continue

        if c.norm == "cumulative":
            # 感知难度曲线：逐关累加。
            # 首关强制以 0 为起点，曲线只看相对形状。
            base = lm.wide[cols[0]].fillna(0).astype(float).copy()
            if len(base):
                base.iloc[0] = 0.0
            vals = base.cumsum()
        else:
            # max_ratio 归一：每关占最高关的百分比，再加权
            acc = pd.Series(0.0, index=lm.wide.index)
            for comp in c.components:
                if comp.metric not in lm.wide.columns:
                    continue
                col = lm.wide[comp.metric].astype(float)
                m = col.max()
                if not np.isfinite(m) or m == 0:
                    continue
                acc = acc + (col / m * 100.0) * (comp.weight / total_w)
            vals = acc

        min_n = c.min_n
        for lv in lm.wide.index:
            v = vals.get(lv, float("nan"))
            if pd.isna(v):
                continue
            # 复合指标的置信区间：用其主成分（权重最大那个）的 n 做诚实性判定
            main_metric = max([x for x in c.components if x.metric in lm.wide.columns],
                              key=lambda x: x.weight).metric
            det = lm.detail_at(main_metric, int(lv))
            extra_records.append({
                "metric": cid,
                "metric_name": c.name,
                "level_id": int(lv),
                "point": float(v),
                "ci_low": float("nan"),
                "ci_high": float("nan"),
                "n": det.get("n", 0),
                "verdict": det.get("verdict", statsx.VERDICT_INSUFFICIENT),
                "verdict_label": det.get("verdict_label", ""),
                "min": float("nan"),
                "max": float("nan"),
            })

        lm.meta[cid] = {"name": c.name, "unit": c.unit, "layer": c.layer,
                        "direction": c.direction, "why": c.why, "caveat": "",
                        "fix": "", "min_n": min_n, "warn_above": None,
                        "warn_below": None, "formula": "composite", "agg": "composite"}

    if extra_records:
        lm.long = pd.concat([lm.long, pd.DataFrame(extra_records)], ignore_index=True)
        wide2 = lm.long.pivot(index="level_id", columns="metric", values="point")
        lm.wide = wide2.reindex(sorted(wide2.index))

    return lm


def split_quadrants(lm: LevelMetrics, onto: Ontology,
              obj_metric: str = "difficulty_index",
              per_metric: str = "perceived_difficulty") -> pd.DataFrame:
    """把每关放进「客观难度 × 感知难度」四象限。"""
    rule = onto.rules.get("perception_gap")
    quad_text = rule.quadrants if rule else {}

    if obj_metric not in lm.wide.columns or per_metric not in lm.wide.columns:
        return pd.DataFrame()

    t = lm.wide[[obj_metric, per_metric]].copy()
    t = t.rename(columns={obj_metric: "客观难度", per_metric: "感知难度"})
    t = t.dropna()
    if t.empty:
        return pd.DataFrame()

    # 用中位数切分高低（关卡少时比用均值稳健）
    obj_cut = t["客观难度"].median()
    per_cut = t["感知难度"].median()

    t["象限"] = [_QUADRANT_NAME[(o >= obj_cut, p >= per_cut)]
                 for o, p in zip(t["客观难度"], t["感知难度"])]
    t["建议"] = t["象限"].map(lambda n: quad_text.get(_QUADRANT_KEY[n], ""))

    # 背离度：客观排名与感知排名的差
    t["客观排名"] = t["客观难度"].rank(ascending=False).astype(int)
    t["感知排名"] = t["感知难度"].rank(ascending=False).astype(int)
    t["排名背离"] = (t["客观排名"] - t["感知排名"]).abs()

    thr = float(rule.threshold) if rule else 1
    t["背离"] = t["排名背离"] >= thr

    return t.reset_index().rename(columns={"level_id": "关卡"})


def player_level_matrix(ds: Dataset, onto: Ontology, metric_id: str) -> pd.DataFrame:
    """返回 (玩家 × 关卡) 的指标值矩阵——后续所有统计的输入。"""
    m = onto.metrics.get(metric_id)
    if m is None:
        raise ValueError("未知指标：%s" % metric_id)
    df = ds.df
    tmp = player_level_frame(df, _as_series(eval_formula_rows(df, m.formula), df.index))

    agg_fn = AGG_FUNCS.get(m.agg, np.median)
    return tmp.pivot_table(index="player_id", columns="level_id", values="_v", aggfunc=agg_fn)
