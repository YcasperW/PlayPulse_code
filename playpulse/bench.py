"""PlayBench：按项目存档体检结果，构建跨项目基准线并比对。"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

MIN_PROJECTS = 3            # 少于这个数的项目，不给基准线，只说"攒不够"
MIN_DEVIATION_PCT = 10.0    # 偏离中位数不到 10% 就当持平，别把 1% 的差距说成"高于同类"
DEFAULT_METRICS = ("duration_sec", "stuck_ratio", "attempts",
                   "difficulty_index", "time_dilation", "frustration", "quit_share")

_UNITS = {"duration_sec": " 秒", "active_sec": " 秒", "stuck_ratio": "",
          "attempts": " 次", "difficulty_index": "", "time_dilation": " 倍",
          "frustration": " 分", "quit_share": ""}

# 项目名 → 目录名：只去掉文件系统不收的字符，中文原样保留
_slug = lambda name: re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "").strip()) or "未命名项目"


def bench_root(root: Optional[str] = None) -> str:
    """基准库根目录。默认落在项目根的 data/bench 下，跟运行目录无关。"""
    if root:
        return root
    env = os.environ.get("PLAYBENCH_HOME")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "bench")


def project_dir(project: str, root: Optional[str] = None) -> str:
    """某个项目的存档目录（不存在就建）。"""
    d = os.path.join(bench_root(root), _slug(project))
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class RunRecord:
    """一次体检的存档摘要。"""

    project: str
    run_id: str
    saved_at: str
    build_version: str
    n_players: int
    levels: List[int]
    is_synthetic: bool
    sources: List[str] = field(default_factory=list)
    note: str = ""
    ontology_version: str = ""
    metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    curve: dict = field(default_factory=dict)
    findings: List[dict] = field(default_factory=list)


@dataclass
class Baseline:
    """基准线：每个（指标 × 关卡序号）在 N 个项目上的分位分布。"""

    table: pd.DataFrame
    n_projects: int
    projects: List[str]
    min_projects: int
    metrics: List[str]
    levels: List[int]
    built_at: str = ""

    def row_at(self, metric: str, level: int) -> Optional[dict]:
        """取某个（指标, 关卡）的基准行，没有就返回 None。"""
        hit = self.table[(self.table["metric"] == metric) & (self.table["level_id"] == level)]
        return None if hit.empty else hit.iloc[0].to_dict()

    def is_trustworthy(self, metric: str, level: int) -> bool:
        """这一格的基准够不够格当基准（项目数达标才算）。"""
        r = self.row_at(metric, level)
        return bool(r and r["n_projects"] >= self.min_projects)


@dataclass
class BenchCompare:
    """本次体检 vs 基准线的对比结果。"""

    project: str
    baseline: Baseline
    table: pd.DataFrame
    conclusions: List[str]
    warnings: List[str]
    n_projects: int

    def verdict_sentence(self) -> str:
        """一句话总评；项目数不够时直说不够，不下位置结论。"""
        if self.n_projects < self.baseline.min_projects:
            return ("基准线目前只有 %d 个项目（需要 %d 个），现在还不能说你在同类里排什么位置。"
                    % (self.n_projects, self.baseline.min_projects))
        if self.table.empty:
            return "本次没有可与基准线对齐的通用层指标。"
        hit = self.table[(self.table["值得关注"]) & (self.table["基准可信"])]
        if hit.empty:
            return ("与 %d 个同类项目相比，本次各关都落在正常区间（P25–P75 之间）。"
                    % self.n_projects)
        top = hit.iloc[0]
        more = "；另有 %d 项偏离基准" % (len(hit) - 1) if len(hit) > 1 else ""
        return ("与 %d 个同类项目相比，%s%s。"
                % (self.n_projects, top["结论"], more))


def save_run(pc, project: str, note: str = "", root: Optional[str] = None) -> str:
    """把一次体检结果存进这个项目自己的目录，返回落盘路径。"""
    onto = pc.ontology
    wide = pc.level_metrics.wide
    metrics: Dict[str, Dict[str, float]] = {}

    for mid in _universal_metric_ids(onto):
        if mid not in wide.columns:
            continue
        by_level = {}
        for lv in wide.index:
            v = wide.loc[lv, mid]
            if pd.notna(v):
                by_level[str(int(lv))] = round(float(v), 4)
        if by_level:
            metrics[mid] = by_level

    now = datetime.datetime.now()
    run_id = now.strftime("%Y%m%d-%H%M%S")
    ds = pc.dataset
    rec = RunRecord(
        project=project,
        run_id=run_id,
        saved_at=now.strftime("%Y-%m-%d %H:%M"),
        build_version="、".join(map(str, ds.versions)),
        n_players=int(pc.n_players),
        levels=[int(x) for x in ds.levels],
        is_synthetic=bool(ds.is_synthetic),
        sources=[os.path.basename(s) for s in ds.sources],
        note=note,
        ontology_version=str(getattr(onto, "version", "")),
        metrics=metrics,
        curve=dict(pc.curve or {}),
        findings=[{"rule_id": f.rule_id, "name": f.name, "severity": f.severity,
                   "level": f.level, "value": f.value} for f in pc.findings],
    )

    path = os.path.join(project_dir(project, root), "%s.json" % run_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec.__dict__, f, ensure_ascii=False, indent=2)
    return path


def load_runs(project: str, root: Optional[str] = None) -> List[RunRecord]:
    """读某个项目的全部存档，按时间正序（旧的在前）。"""
    out: List[RunRecord] = []
    for p in sorted(glob.glob(os.path.join(project_dir(project, root), "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        d.setdefault("project", project)
        for k in ("metrics", "curve", "findings", "levels", "sources"):
            d.setdefault(k, {} if k in ("metrics", "curve") else [])
        out.append(RunRecord(**{k: v for k, v in d.items()
                                if k in RunRecord.__dataclass_fields__}))
    return out


def list_projects(root: Optional[str] = None) -> pd.DataFrame:
    """列出基准库里的所有项目：记录数、最近一次体检、覆盖到几关。"""
    base = bench_root(root)
    rows = []
    if not os.path.isdir(base):
        return pd.DataFrame(columns=["项目", "记录数", "最近体检", "玩家数", "关卡数", "含合成数据"])
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        runs = load_runs(name, root)
        if not runs:
            continue
        last = runs[-1]
        rows.append({
            "项目": last.project or name,
            "记录数": len(runs),
            "最近体检": last.saved_at,
            "玩家数": last.n_players,
            "关卡数": len(last.levels),
            "含合成数据": "是" if any(r.is_synthetic for r in runs) else "",
        })
    return pd.DataFrame(rows)


def latest_run_per_project(exclude: Optional[str] = None,
                           root: Optional[str] = None) -> Dict[str, RunRecord]:
    """每个项目取最近一条存档。"""
    base = bench_root(root)
    out: Dict[str, RunRecord] = {}
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if not os.path.isdir(os.path.join(base, name)):
            continue
        runs = load_runs(name, root)
        if not runs:
            continue
        last = runs[-1]
        if exclude and _slug(last.project) == _slug(exclude):
            continue
        out[last.project or name] = last
    return out


def build_baseline(exclude: Optional[str] = None, metrics: Optional[List[str]] = None,
                   min_projects: int = MIN_PROJECTS,
                   root: Optional[str] = None) -> Baseline:
    """把各项目最近一次体检摊平，按（指标 × 关卡）算分位数。"""
    runs = latest_run_per_project(exclude, root)
    rows = []
    for proj, run in runs.items():
        for mid, by_level in (run.metrics or {}).items():
            if metrics and mid not in metrics:
                continue
            for lv, v in by_level.items():
                rows.append({"project": proj, "metric": mid,
                             "level_id": int(lv), "value": float(v)})

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if not rows:
        return Baseline(table=pd.DataFrame(), n_projects=0, projects=[],
                        min_projects=min_projects, metrics=[], levels=[], built_at=now)

    df = pd.DataFrame(rows)
    recs = []
    for (mid, lv), g in df.groupby(["metric", "level_id"], sort=True):
        v = g["value"].to_numpy(dtype=float)
        recs.append({
            "metric": mid,
            "level_id": int(lv),
            "n_projects": int(len(v)),
            "p10": float(np.percentile(v, 10)),
            "p25": float(np.percentile(v, 25)),
            "p50": float(np.percentile(v, 50)),
            "p75": float(np.percentile(v, 75)),
            "p90": float(np.percentile(v, 90)),
            "min": float(v.min()),
            "max": float(v.max()),
        })
    table = pd.DataFrame(recs).sort_values(["metric", "level_id"]).reset_index(drop=True)
    table["基准可信"] = table["n_projects"] >= min_projects

    return Baseline(table=table, n_projects=len(runs), projects=sorted(runs.keys()),
                    min_projects=min_projects,
                    metrics=sorted(df["metric"].unique().tolist()),
                    levels=sorted(int(x) for x in df["level_id"].unique()),
                    built_at=now)


def judge_against_baseline(baseline: Baseline, metric: str, metric_name: str,
                           level: int, value: float, direction: str) -> Optional[dict]:
    """把单个数值放到基准线里判一次：比中位数高/低多少、落在哪个区间、值不值得关注。"""
    r = baseline.row_at(metric, level)
    if r is None or pd.isna(value):
        return None

    p25, p50, p75 = r["p25"], r["p50"], r["p75"]
    delta = float(value) - p50
    delta_pct = (delta / p50 * 100.0) if p50 else float("nan")
    flat_band = abs(p75 - p25) < 1e-9        # 各项目取值一样，分位数没有离散度
    small = (not np.isnan(delta_pct)) and abs(delta_pct) < MIN_DEVIATION_PCT

    if flat_band:
        band, band_cn = "flat", "基准没有离散度（各项目取值相同），比不出高低"
    elif small:
        band, band_cn = "middle", "与基准持平（偏离不到 %.0f%%）" % MIN_DEVIATION_PCT
    elif value >= p75:
        band, band_cn = "above_p75", "高于 P75（同类里最高的一档）"
    elif value <= p25:
        band, band_cn = "below_p25", "低于 P25（同类里最低的一档）"
    else:
        band, band_cn = "middle", "在 P25–P75 的正常区间里"

    # 只有「明显偏离」且方向是变差的那一侧才值得看；持平和无离散度的不算
    worse = (value > p75 and not small) if direction == "higher_is_worse" else \
            (value < p25 and not small) if direction == "higher_is_better" else False
    attention = bool(worse and not flat_band)

    u = _UNITS.get(metric, "")
    delta_cn = ("%s %s%s" % ("高" if delta >= 0 else "低", _fmtv(abs(delta)), u)
                if abs(delta) >= 0.005 else "基本一样")
    if np.isnan(delta_pct):
        pct_cn = ""                      # 基准中位数是 0，算不出百分比，只说绝对值
    else:
        pct_cn = "（%s%.0f%%）" % ("+" if delta >= 0 else "-", abs(delta_pct))

    if direction == "neutral":
        sentence = "第 %d 关%s %s%s，基准中位数 %s%s，%s" % (
            level, metric_name, _fmtv(float(value)), u, _fmtv(p50), u, band_cn)
    else:
        sentence = "第 %d 关%s %s%s，比基准中位数 %s%s %s%s，%s" % (
            level, metric_name, _fmtv(float(value)), u, _fmtv(p50), u,
            delta_cn, pct_cn, band_cn)

    pct_show = "—" if np.isnan(delta_pct) else "%+.0f%%" % delta_pct
    return {
        "metric": metric, "指标": metric_name, "关卡": "第 %d 关" % level,
        "level_id": level, "本次": round(float(value), 3),
        "基准中位数": round(p50, 3), "P25": round(p25, 3), "P75": round(p75, 3),
        "差": round(delta, 3),
        "差%": pct_show, "_pct": (0.0 if np.isnan(delta_pct) else abs(delta_pct)),
        "位置": band_cn, "_band": band,
        "项目数": int(r["n_projects"]),
        "基准可信": bool(r["n_projects"] >= baseline.min_projects),
        "值得关注": bool(attention),
        "结论": sentence,
    }


def compare_to_baseline(pc, project: Optional[str] = None,
                        metrics: Optional[List[str]] = None,
                        min_projects: int = MIN_PROJECTS,
                        root: Optional[str] = None) -> BenchCompare:
    """本次体检 vs 基准线；默认把当前项目自己排除在外，避免自己跟自己比。"""
    onto = pc.ontology
    metrics = list(metrics or DEFAULT_METRICS)
    baseline = build_baseline(exclude=project, metrics=metrics,
                              min_projects=min_projects, root=root)

    rows = []
    for mid in _universal_metric_ids(onto):
        if metrics and mid not in metrics:
            continue
        if mid not in pc.level_metrics.wide.columns:
            continue
        name = _metric_name(onto, mid)
        direction = _metric_direction(onto, mid)
        for lv in pc.level_metrics.wide.index:
            v = pc.level_metrics.wide.loc[lv, mid]
            if pd.isna(v):
                continue
            j = judge_against_baseline(baseline, mid, name, int(lv), float(v), direction)
            if j:
                rows.append(j)

    table = pd.DataFrame(rows)
    if not table.empty:
        table = table.sort_values(["值得关注", "基准可信", "关卡", "指标"],
                                  ascending=[False, False, True, True]).reset_index(drop=True)

    warnings: List[str] = []
    synthetic = [p for p, r in latest_run_per_project(project, root).items() if r.is_synthetic]
    if synthetic:
        warnings.append("参与基准线的项目里有 %d 个用的是合成数据（%s），"
                        "这类记录只用于验证链路，把基准线当成真实基准会失真。"
                        % (len(synthetic), "、".join(synthetic[:3])))
    if baseline.n_projects and baseline.n_projects < min_projects:
        warnings.append("基准线目前只有 %d 个项目，不足 %d 个门槛，"
                        "下面的数字只能看趋势，不能当成基准。"
                        % (baseline.n_projects, min_projects))

    conclusions = _compare_conclusions(table)
    return BenchCompare(project=project or "本次", baseline=baseline, table=table,
                        conclusions=conclusions, warnings=warnings,
                        n_projects=baseline.n_projects)


def _compare_conclusions(table: pd.DataFrame, top: int = 3) -> List[str]:
    """挑出值得关注且基准可信的行，按偏离幅度排序，最多给 top 条。"""
    if table.empty:
        return []
    hit = table[(table["值得关注"]) & (table["基准可信"])].copy()
    if hit.empty:
        return []
    return hit.sort_values("_pct", ascending=False)["结论"].head(top).tolist()


def _fmtv(v: float) -> str:
    """按量级决定小数位：大数一位小数，小数三位——0.191 不能显示成 0.2。"""
    a = abs(float(v))
    return "%.1f" % v if a >= 1 else "%.3f" % v


def _universal_metric_ids(onto) -> List[str]:
    """本体里所有通用层指标的 id（含复合指标），基准线只认这一层。"""
    ids = [m for m, d in getattr(onto, "metrics", {}).items() if d.layer == "universal"]
    ids += [m for m, d in getattr(onto, "composites", {}).items() if d.layer == "universal"]
    return ids


# 基础指标和复合指标分两个桶存，取定义时不关心它在哪个桶里
def _metric_def(onto, metric_id: str):
    for bucket in ("metrics", "composites"):
        got = getattr(onto, bucket, {}).get(metric_id)
        if got is not None:
            return got
    return None


def _metric_name(onto, metric_id: str) -> str:
    """指标的中文名；本体里没有就原样回 id，不编一个。"""
    d = _metric_def(onto, metric_id)
    return d.name if d else metric_id


def _metric_direction(onto, metric_id: str) -> str:
    """指标方向：higher_is_worse / higher_is_better / neutral。"""
    d = _metric_def(onto, metric_id)
    return d.direction if d else "neutral"


def baseline_table_for_display(baseline: Baseline, onto=None) -> pd.DataFrame:
    """把基准线整理成能直接给人看的表（加中文名、单位、可信标记）。"""
    if baseline.table.empty:
        return baseline.table
    t = baseline.table.copy()
    t.insert(1, "指标", [(_metric_name(onto, m) if onto else m) for m in t["metric"]])
    t["关卡"] = ["第 %d 关" % int(x) for x in t["level_id"]]
    t["基准可信"] = t["基准可信"].map(lambda b: "是" if b else "否")
    for c in ("p10", "p25", "p50", "p75", "p90", "min", "max"):
        t[c] = t[c].round(3)
    return t[["指标", "metric", "关卡", "level_id", "n_projects",
              "p10", "p25", "p50", "p75", "p90", "min", "max", "基准可信"]]


