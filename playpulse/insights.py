"""洞察层：执行 metrics.yaml 中的规则，产出发现与归因分布。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import schema, statsx
from .io_load import Dataset
from .semantics import Ontology
from .semantics.compute import (LevelMetrics, compute_composites, compute_level_metrics,
                                split_quadrants)


@dataclass
class Finding:
    """一条洞察结论。"""
    rule_id: str
    name: str
    severity: str                 # high / medium / low
    level: Optional[int] = None   # 涉及的关卡（None = 全局）
    metric: str = ""
    value: float = float("nan")
    threshold: float = float("nan")
    n: int = 0
    verdict: str = statsx.VERDICT_CONCLUSION
    verdict_label: str = ""
    conclusion: str = ""          # 人话结论
    advice: str = ""              # 怎么改
    evidence: dict = field(default_factory=dict)

    # 该发现的样本量是否达到「可下结论」
    @property
    def is_trustworthy(self) -> bool:
        return self.verdict == statsx.VERDICT_CONCLUSION

    # 把一条发现摊平成报告表格的一行
    def to_row(self) -> dict:
        return {
            "严重度": {"high": "高", "medium": "中", "low": "低"}.get(self.severity, self.severity),
            "问题": self.name,
            "关卡": self.level if self.level is not None else "全局",
            "结论": self.conclusion,
            "建议": self.advice,
            "样本": self.n,
            "可信度": self.verdict_label,
        }


@dataclass
class PulseCheck:
    """一次完整的难度体检结果。"""
    findings: List[Finding]
    level_metrics: LevelMetrics
    quadrants: pd.DataFrame
    attribution: pd.DataFrame        # 卡关归因分布（按关卡）
    attribution_overall: pd.DataFrame
    choice_dist: pd.DataFrame        # 卡关三选一分布（按关卡）
    choice_overall: pd.DataFrame
    curve: dict                      # 难度曲线形状评估
    dataset: Dataset
    ontology: Ontology
    n_players: int = 0

    # 按严重度筛选发现
    def findings_by_severity(self, sev: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == sev]

    # 发现排序：先严重度后关卡
    def findings_sorted(self) -> List[Finding]:
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(self.findings, key=lambda f: (order.get(f.severity, 9),
                                                    f.level if f.level is not None else -1))

    # 发现列表转 DataFrame
    def findings_as_table(self) -> pd.DataFrame:
        return pd.DataFrame([f.to_row() for f in self.findings_sorted()])


def run_pulse_check(ds: Dataset, onto: Optional[Ontology] = None) -> PulseCheck:
    """跑一次完整的难度体检。"""
    if onto is None:
        from .semantics import default_ontology
        onto = default_ontology()

    lm = compute_level_metrics(ds, onto)
    lm = compute_composites(lm, onto)

    findings: List[Finding] = []
    findings += detect_difficulty_jump(lm, onto, "cliff")
    findings += detect_difficulty_jump(lm, onto, "hard_block")
    findings += detect_threshold_breach(lm, onto, "dilation_alert")
    findings += detect_threshold_breach(lm, onto, "stuck_alert")
    findings += detect_threshold_breach(lm, onto, "quit_alert")
    findings += _detect_perception_gap(lm, onto)
    findings += detect_curve_problem(lm, onto)

    attr, attr_all = count_codes_by_level(ds, "stuck_reason_code", schema.STUCK_REASON_CODES,
                                        "归因", "修法方向", "fix")
    choice, choice_all = count_codes_by_level(ds, "stuck_choice_code", schema.STUCK_CHOICE_CODES,
                                            "选择", "含义", "meaning")
    curve = describe_curve_shape(lm, onto)

    return PulseCheck(
        findings=findings, level_metrics=lm, quadrants=split_quadrants(lm, onto),
        attribution=attr, attribution_overall=attr_all,
        choice_dist=choice, choice_overall=choice_all,
        curve=curve, dataset=ds, ontology=onto, n_players=ds.n_players,
    )


# 规则的三件前置检查：规则在不在、作用的指标算没算出来、阈值是多少。
# 任一项不满足返回 None
def _rule_setup(lm: LevelMetrics, onto: Ontology, rule_id: str):
    rule = onto.rules.get(rule_id)
    if rule is None:
        return None
    # 规则配置里的键名是 target（YAML 里不能写裸 `on`，会被解析成布尔值）
    metric = getattr(rule, "target", None) or rule.on
    if metric not in lm.wide.columns:
        return None
    return rule, metric, float(rule.threshold), onto.metrics.get(metric)


# 样本不够时给结论补一句「这只是方向」
def _weaken_if_weak(conclusion: str, verdict: str, n: int) -> str:
    if verdict == statsx.VERDICT_CONCLUSION:
        return conclusion
    return conclusion + "。**样本仅 %d 人，此结论只作方向参考**" % n


# 相邻关卡跳变检测：断崖、尝试次数骤增
def detect_difficulty_jump(lm: LevelMetrics, onto: Ontology, rule_id: str) -> List[Finding]:
    setup = _rule_setup(lm, onto, rule_id)
    if setup is None:
        return []
    rule, metric, thr, mdef = setup

    levels = sorted(lm.wide.index)
    out: List[Finding] = []
    unit = (mdef.unit if mdef else "") or ""

    for prev, cur in zip(levels, levels[1:]):
        a = lm.value_at(metric, int(prev))
        b = lm.value_at(metric, int(cur))
        if not np.isfinite(a) or not np.isfinite(b) or a <= 0:
            continue
        ratio = b / a
        if ratio < thr:
            continue

        det = lm.detail_at(metric, int(cur))
        n = int(det.get("n", 0))
        verdict = det.get("verdict", statsx.VERDICT_INSUFFICIENT)
        ci_a = det.get("ci_low"), det.get("ci_high")

        name = "%s：第 %d 关" % (rule.name, int(cur))
        mname = (mdef.name if mdef else metric)
        conclusion = ("第 %d 关「%s」是第 %d 关的 **%.2f 倍**（%.1f%s → %.1f%s），"
                      "超过阈值 %.1f 倍" % (int(cur), mname, int(prev), ratio, a, unit, b, unit, thr))
        conclusion = _weaken_if_weak(conclusion, verdict, n)

        out.append(Finding(
            rule_id=rule_id, name=name, severity=rule.severity,
            level=int(cur), metric=metric, value=ratio, threshold=thr,
            n=n, verdict=verdict, verdict_label=det.get("verdict_label", ""),
            conclusion=conclusion, advice=rule.fix,
            evidence={"prev_level": int(prev), "prev_value": a, "cur_value": b,
                      "ratio": ratio, "ci": ci_a},
        ))
    return out


# 阈值类规则检测：卡关占比、时间膨胀、准流失
def detect_threshold_breach(lm: LevelMetrics, onto: Ontology, rule_id: str) -> List[Finding]:
    setup = _rule_setup(lm, onto, rule_id)
    if setup is None:
        return []
    rule, metric, thr, mdef = setup

    name_hint = (mdef.name if mdef else metric)
    unit = (mdef.unit if mdef else "") or ""
    out: List[Finding] = []

    for lv in sorted(lm.wide.index):
        v = lm.value_at(metric, int(lv))
        if not np.isfinite(v):
            continue
        hit = v >= thr
        if not hit:
            continue

        det = lm.detail_at(metric, int(lv))
        n = int(det.get("n", 0))
        verdict = det.get("verdict", statsx.VERDICT_INSUFFICIENT)

        if metric == "time_dilation":
            conclusion = ("第 %d 关时间膨胀系数 **%.2f**（> %.1f）：玩家觉得比实际更漫长，"
                          "典型症状是枯燥或干等。实际耗时 %.0fs，玩家以为花了 %.0fs"
                          % (int(lv), v, thr,
                             lm.value_at("duration_sec", int(lv)) if "duration_sec" in lm.wide.columns else float("nan"),
                             lm.value_at("estimated_duration_sec", int(lv)) if "estimated_duration_sec" in lm.wide.columns else float("nan")))
        elif metric == "quit_share":
            conclusion = ("第 %d 关有 **%.0f%%** 的人卡住时选择「直接退出」（> %.0f%%），"
                          "这是最强流失信号" % (int(lv), v * 100, thr * 100))
        elif metric == "stuck_ratio":
            conclusion = ("第 %d 关卡关占比 **%.0f%%**（> %.0f%%）：这关大部分时间在干等"
                          % (int(lv), v * 100, thr * 100))
        else:
            conclusion = "第 %d 关%s = %.2f%s，超过阈值 %.2f" % (int(lv), name_hint, v, unit, thr)
        conclusion = _weaken_if_weak(conclusion, verdict, n)

        out.append(Finding(
            rule_id=rule_id, name="%s：第 %d 关" % (rule.name, int(lv)),
            severity=rule.severity, level=int(lv), metric=metric,
            value=v, threshold=thr, n=n, verdict=verdict,
            verdict_label=det.get("verdict_label", ""),
            conclusion=conclusion, advice=rule.fix or (mdef.fix if mdef else ""),
            evidence={"value": v, "ci_low": det.get("ci_low"), "ci_high": det.get("ci_high")},
        ))

    # 时间膨胀还有一个反向信号：全关卡都 < 0.8，可能是无决策的挂机消耗
    if metric == "time_dilation" and "time_dilation" in lm.wide.columns:
        vals = lm.wide["time_dilation"].dropna()
        flow_thr = float(onto.thresholds.get("time_dilation_flow", 0.8))
        if len(vals) >= 2 and (vals < flow_thr).all():
            out.append(Finding(
                rule_id=rule_id, name="全部关卡进入心流区间（需复核）",
                severity="low", level=None, metric=metric,
                value=float(vals.mean()), threshold=flow_thr,
                n=int(lm.long[lm.long["metric"] == metric]["n"].max() or 0),
                verdict=statsx.VERDICT_CONCLUSION, verdict_label="复核项",
                conclusion=("所有关卡的时间膨胀系数都 < %.1f（均值 %.2f）。"
                            "这通常是好事，但也可能是**无决策的挂机消耗**——"
                            "玩家没感到时间流逝，是因为根本没在思考"
                            % (flow_thr, float(vals.mean()))),
                advice="结合挑战感分数看：若挑战感也偏低，说明这关只是无感消耗，需要加决策点",
            ))
    return out


# 四象限背离检测：客观与感知排名差超过阈值
def _detect_perception_gap(lm: LevelMetrics, onto: Ontology) -> List[Finding]:
    rule = onto.rules.get("perception_gap")
    if rule is None:
        return []
    q = split_quadrants(lm, onto)
    if q.empty or "背离" not in q.columns:
        return []

    out: List[Finding] = []
    for _, r in q.iterrows():
        if not bool(r.get("背离", False)):
            continue
        lv = int(r["关卡"])
        quad = r["象限"]
        det = lm.detail_at("difficulty_index", lv)
        n = int(det.get("n", 0))
        verdict = det.get("verdict", statsx.VERDICT_INSUFFICIENT)

        conclusion = ("第 %d 关属于「**%s**」：客观难度排第 %d、感知难度排第 %d，两者背离。"
                      % (lv, quad, int(r["客观排名"]), int(r["感知排名"])))
        conclusion = _weaken_if_weak(conclusion, verdict, n)

        severity = rule.severity if quad != "心流/无感消耗" else "low"
        out.append(Finding(
            rule_id="perception_gap", name="客观/感知背离：第 %d 关" % lv,
            severity=severity, level=lv, metric="difficulty_index",
            value=float(r["客观难度"]), threshold=float(rule.threshold),
            n=n, verdict=verdict, verdict_label=det.get("verdict_label", ""),
            conclusion=conclusion, advice=str(r.get("建议", "")),
            evidence={"象限": quad, "客观难度": float(r["客观难度"]),
                      "感知难度": float(r["感知难度"])},
        ))
    return out


def describe_curve_shape(lm: LevelMetrics, onto: Ontology) -> dict:
    """评估难度曲线的形状。"""
    if "duration_sec" not in lm.wide.columns or len(lm.wide) < 2:
        return {}

    d = lm.wide["duration_sec"].dropna()
    vals = [float(x) for x in d.tolist()]
    deltas = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    ratios = [vals[i] / vals[i - 1] if vals[i - 1] else float("nan")
              for i in range(1, len(vals))]

    rising = sum(1 for x in deltas if x > 0)
    flat = sum(1 for x in deltas if abs(x) <= max(2.0, 0.05 * vals[0]))
    dips = sum(1 for x in deltas if x < 0)

    if not deltas:
        return {}
    if dips >= 1:
        shape = "锯齿上升"
        comment = "有起伏、有喘息，这是健康曲线的形状"
    elif rising == len(deltas) and max(ratios) < 1.15:
        shape = "平缓上升"
        comment = "整体太平，缺少节奏起伏，玩家容易觉得「每关都一样」"
    elif rising == len(deltas):
        shape = "直线上升"
        comment = "持续加压没有喘息，容易累积疲劳"
    elif flat >= len(deltas) / 2:
        shape = "平台期"
        comment = "多关难度几乎不变，中段的动力会掉"
    else:
        shape = "不规则"
        comment = "曲线无明显规律，需结合断崖点判断"

    return {
        "shape": shape,
        "comment": comment,
        "values": vals,
        "ratios": [float(r) for r in ratios],
        "max_ratio": float(np.nanmax(ratios)) if ratios else float("nan"),
        "target": "锯齿上升（有起伏有喘息）",
    }


# 曲线形状不是锯齿上升时，给一条低优先级提示
def detect_curve_problem(lm: LevelMetrics, onto: Ontology) -> List[Finding]:
    info = describe_curve_shape(lm, onto)
    if not info or info["shape"] in ("锯齿上升",):
        return []
    rule = onto.rules.get("cliff")
    return [Finding(
        rule_id="curve_shape", name="难度曲线形状：%s" % info["shape"],
        severity="low", level=None, metric="duration_sec",
        value=info["max_ratio"], threshold=1.0,
        n=lm.n_players_total, verdict=statsx.VERDICT_CONCLUSION,
        verdict_label="形状评估",
        conclusion="难度曲线呈「%s」。%s" % (info["shape"], info["comment"]),
        advice=("目标形状是「%s」——%s"
                % (info["target"], (rule.target_shape if rule else ""))),
        evidence=info,
    )]


def count_codes_by_level(ds: Dataset, col: str, codebook: dict,
                       name_col: str, extra_col: str, extra_key: str) -> tuple:
    """统计编码字段的分布，返回 (逐关, 整体)。编码 0 = 没卡住，不计入。"""
    df = ds.df
    if col not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    tmp = df.assign(_code=pd.to_numeric(df[col], errors="coerce"))
    hit = tmp[tmp["_code"].notna() & (tmp["_code"] != 0)]
    if hit.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 统计一组记录内的编码占比，按占比降序
    build = lambda sub: pd.DataFrame([
        {name_col: codebook.get(int(code), {}).get("label", "未知"), "编码": int(code),
         "次数": len(g), "占比": len(g) / len(sub) if len(sub) else 0.0,
         extra_col: codebook.get(int(code), {}).get(extra_key, "")}
        for code, g in sub.groupby("_code")
    ]).sort_values("占比", ascending=False).reset_index(drop=True)

    levels = pd.to_numeric(hit["level_id"], errors="coerce")
    per_level = []
    for lv in sorted(int(x) for x in levels.dropna().unique()):
        b = build(hit[levels == lv])
        if not b.empty:
            b.insert(0, "关卡", lv)
            per_level.append(b)

    by_level = pd.concat(per_level, ignore_index=True) if per_level else pd.DataFrame()
    return by_level, build(hit)
