"""报告生成：构建 sections，供 Markdown / HTML / Excel 渲染。"""

from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import statsx
from .charts import (fig_difficulty_curve, fig_quadrants, fig_distribution,
                     fig_version_diff, fig_metric_bars)
from .bench import BenchCompare
from .diff import VersionDiff
from .insights import PulseCheck
from .semantics import Ontology


# 报告块 DSL：build_report 只负责往 sections 里堆这些块，
# 两个渲染器各自把同一份块渲染成 MD / HTML
H = lambda level, text: {"type": "heading", "level": level, "text": text}
P = lambda text: {"type": "para", "text": text}
CALLOUT = lambda text, kind="info": {"type": "callout", "text": text, "kind": kind}  # info/warn/danger/ok
TABLE = lambda df, caption="", index=False: {"type": "table", "df": df, "caption": caption, "index": index}
IMG = lambda b64, caption="": {"type": "image", "b64": b64, "caption": caption}
LIST = lambda items, ordered=True: {"type": "list", "items": items, "ordered": ordered}
RAW = lambda text: {"type": "raw", "text": text}

_ICONS = {"info": "ℹ️", "warn": "⚠️", "danger": "🔴", "ok": "✅"}
# 章节编号自增，插节时不用改后面的编号
_CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
           "十一", "十二", "十三", "十四", "十五"]
# HTML 转义
_esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
# 把 粗体 和 `代码` 转成 HTML 标签
_md_inline = lambda s: re.sub(r"`(.+?)`", r"<code>\1</code>",
                              re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(s)))


@dataclass
class Report:
    title: str
    subtitle: str
    sections: List[dict]
    markdown: str
    html: str
    generated_at: str
    meta: Dict[str, object] = field(default_factory=dict)

    def save_files(self, out_dir: str, basename: str = "难度体检报告",
                   excel: bool = True) -> Dict[str, str]:
        """保存成 md + html + xlsx 三个文件，返回路径。"""
        os.makedirs(out_dir, exist_ok=True)
        md_path = os.path.join(out_dir, basename + ".md")
        html_path = os.path.join(out_dir, basename + ".html")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.markdown)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(self.html)
        paths = {"markdown": md_path, "html": html_path}
        if excel:
            from .export_excel import save_excel
            paths["excel"] = save_excel(self, os.path.join(out_dir, basename + ".xlsx"))
        return paths


def build_report(pc: PulseCheck, diff: Optional[VersionDiff] = None,
                 title: Optional[str] = None,
                 notes: Optional[List[str]] = None,
                 with_charts: bool = True,
                 bench: Optional[BenchCompare] = None) -> Report:
    """构建一份难度体检报告。"""
    ds = pc.dataset
    onto = pc.ontology
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    _n = [0]

    def _sec(text: str) -> dict:
        """带自增中文编号的章节标题。"""
        _n[0] += 1
        return H(2, "%s、%s" % (_CN_NUM[_n[0] - 1], text))

    def _draw(draw, caption: str) -> None:
        """画一张图插进报告。画不出来（缺字段）就整块不出现，不留空图占位。"""
        if not with_charts:
            return
        b64 = draw()
        if b64:
            S.append(IMG(b64, caption))

    subtitle_parts = [
        "%d 位玩家" % pc.n_players,
        "%d 个关卡" % len(ds.levels),
        "版本 %s" % "、".join(map(str, ds.versions)),
    ]
    subtitle = " · ".join(subtitle_parts)

    S: List[dict] = []

    # 0. 概览
    S.append(_sec("这次体检的基本信息"))
    overview = pd.DataFrame([
        {"项目": "数据来源", "值": "、".join(os.path.basename(s) for s in ds.sources) or "—"},
        {"项目": "玩家数（样本量单位）", "值": "%d 人" % pc.n_players},
        {"项目": "关卡数", "值": "%d 关" % len(ds.levels)},
        {"项目": "游戏版本", "值": "、".join(map(str, ds.versions))},
        {"项目": "记录行数", "值": "%d 行" % ds.n_rows},
        {"项目": "生成时间", "值": now},
    ])
    S.append(TABLE(overview, caption="表 1 数据概览"))

    if ds.is_synthetic:
        S.append(CALLOUT(
            "**本批数据包含合成（仿真）数据。** 结论仅用于验证系统链路与演示，"
            "不能作为真实玩家的难度结论使用。合成数据的生成方式与局限性见 "
            "`data/raw/合成数据说明.md`。", "warn"))

    # 1. 一句话总评
    S.append(_sec("一句话结论"))
    S.append(CALLOUT(_headline_verdict(pc), "danger" if pc.findings_by_severity("high") else "info"))

    # 可信度总声明
    weak = [f for f in pc.findings if not f.is_trustworthy]
    if weak:
        S.append(CALLOUT(
            "有 %d 条结论的样本量不足，**只代表方向，不代表结论**，不要据此直接改数值。"
            % len(weak), "warn"))

    # 2. 难度曲线
    S.append(_sec("难度曲线与断崖点"))
    _draw(lambda: fig_difficulty_curve(pc.level_metrics, onto),
          "图 1 难度曲线（阴影为 95% 置信区间，红色标注为断崖点）")

    curve = pc.curve or {}
    if curve:
        S.append(P("曲线形状判定：**%s** —— %s。目标形状是「%s」。"
                   % (curve.get("shape", "—"), curve.get("comment", ""),
                      curve.get("target", "锯齿上升"))))

    cliffs = [f for f in pc.findings if f.rule_id in ("cliff", "hard_block")]
    if cliffs:
        for f in cliffs:
            S.append(CALLOUT("%s：%s" % (f.name, f.conclusion),
                             "danger" if f.severity == "high" else "warn"))
    else:
        S.append(P("未检测到断崖（相邻关卡耗时跳变未超过 1.5 倍阈值）。"))

    # 3. 关键发现
    S.append(_sec("关键发现"))
    if pc.findings:
        S.append(TABLE(pc.findings_as_table(),
                       caption="表 2 自动检测到的全部问题（按严重度排序）"))
    else:
        S.append(P("本次未触发任何异常规则。这不代表一定没问题——"
                   "也可能是样本量太小，不足以触发判定。"))

    # 4. 逐关指标
    S.append(_sec("逐关指标明细"))
    S.append(TABLE(_level_detail_table(pc),
                   caption="表 3 每关每个指标的中位数与 95% 置信区间"))
    S.append(P("**读法**：置信区间越宽，说明这个数越不确定。区间宽到横跨半个量程时，"
               "这个指标基本只能看个方向。"))

    # 5. 四象限
    S.append(_sec("客观难度 vs 感知难度"))
    if not pc.quadrants.empty:
        _draw(lambda: fig_quadrants(pc.quadrants),
              "图 2 四象限散点（每个点是一关，L 后为关卡号）")
        q = pc.quadrants[["关卡", "客观难度", "感知难度", "象限", "建议"]].copy()
        q["客观难度"] = q["客观难度"].round(1)
        q["感知难度"] = q["感知难度"].round(2)
        S.append(TABLE(q, caption="表 4 四象限判定"))
        S.append(P("**客观与感知不一致的关卡，就是设计问题所在**。"
                   "「假性难度」尤其值得注意：数据上不难，但玩家觉得难，"
                   "这通常不是数值问题，改数值无效。"))
    else:
        S.append(P("字段不足，无法计算四象限（需要难度增量或主观评分字段）。"))

    # 6. 卡关归因
    S.append(_sec("卡关归因（决定怎么修）"))
    if not pc.attribution_overall.empty:
        _draw(lambda: fig_distribution(pc.attribution_overall, "归因",
                                       title="卡关原因分布", color="#7c3aed"),
              "图 3 卡关原因占比")
        a = pc.attribution_overall.copy()
        a["占比"] = (a["占比"] * 100).round(1).astype(str) + "%"
        S.append(TABLE(a[["归因", "占比", "次数", "修法方向"]], caption="表 5 卡关原因分布"))
        top = pc.attribution_overall.iloc[0]
        S.append(CALLOUT(
            "占比最高的归因是「**%s**」（%.0f%%），这意味着主要修法是：**%s**。"
            "归因错了会导致乱调数值——比如把「没看懂规则」当成「资源不够」去调产出，"
            "数值越调越崩。" % (top["归因"], top["占比"] * 100, top["修法方向"]), "info"))
    else:
        S.append(P("无卡关归因数据（缺少 `卡关原因` 列）。"))

    # 7. 卡关选择
    S.append(_sec("卡住时玩家会选什么（变现位与流失信号）"))
    if not pc.choice_overall.empty:
        _draw(lambda: fig_distribution(pc.choice_overall, "选择",
                                       title="卡关三选一分布", color="#059669"),
              "图 4 卡关时的选择分布")
        c = pc.choice_overall.copy()
        c["占比"] = (c["占比"] * 100).round(1).astype(str) + "%"
        S.append(TABLE(c[["选择", "占比", "次数", "含义"]], caption="表 6 卡关选择分布"))
        quit_row = pc.choice_overall[pc.choice_overall["编码"] == 4]
        if not quit_row.empty:
            S.append(CALLOUT("选「直接退出」的人占 %.0f%%，这是最强流失信号，"
                             "等同准流失点。" % (float(quit_row["占比"].iloc[0]) * 100),
                             "danger"))
        ad_row = pc.choice_overall[pc.choice_overall["编码"] == 2]
        if not ad_row.empty:
            S.append(P("选「看广告」的人占 %.0f%% ——这是广告位的**自然触发率**，"
                       "说明玩家真的卡住了才会看，点位放对了地方。"
                       % (float(ad_row["占比"].iloc[0]) * 100)))
    else:
        S.append(P("无卡关选择数据。"))

    # 8. 版本对比
    if diff is not None:
        S.append(_sec("版本对比"))
        _draw(lambda: fig_version_diff(diff), "图 5 版本对比：难度曲线前后叠图")
        S.append(CALLOUT(diff.verdict_sentence(), "info"))
        if diff.conclusions:
            S.append(LIST(diff.conclusions[1:], ordered=False))
        if not diff.table.empty:
            t = diff.table.copy()
            for col in t.columns:
                if t[col].dtype.kind == "f":
                    t[col] = t[col].round(3)
            S.append(TABLE(t, caption="表 7 逐关逐指标对比（新 vs 旧）"))
        if diff.warnings:
            for w in diff.warnings:
                S.append(CALLOUT(w, "warn"))
        S.append(P("**怎么读「超出噪声」**：差值置信区间如果跨过 0，就意味着"
                   "「这个变化可能只是随机波动」。这类差异不要写进汇报。"))

    # 8.5 与基准线的对比（有才出现）
    if bench is not None:
        S.append(_sec("与基准线的对比（PlayBench）"))
        short = bench.n_projects < bench.baseline.min_projects
        S.append(CALLOUT(bench.verdict_sentence(), "warn" if short else "info"))
        if not short:
            S.append(P("基准线由 %d 个项目各自最近一次体检构成（%s）：一个项目一票，"
                       "测了很多次的项目不会把基准拽过去。每一格的样本量单独报，"
                       "因为各项目关卡数不一样。"
                       % (bench.n_projects, "、".join(bench.baseline.projects[:8]))))
        if bench.table.empty:
            S.append(P("本次没有可与基准线对齐的通用层指标（缺字段，或关卡对不上）。"))
        else:
            show = bench.table[["指标", "关卡", "本次", "基准中位数", "P25", "P75",
                                "差%", "位置", "项目数", "基准可信"]].copy()
            show["基准可信"] = show["基准可信"].map(lambda b: "是" if b else "否")
            S.append(TABLE(show, caption="表 · 本次 vs 基准线（P25–P75 是同类的正常区间）"))
            if bench.conclusions:
                S.append(LIST(bench.conclusions, ordered=False))
        for w in bench.warnings:
            S.append(CALLOUT(w, "warn"))

    # 9. 可执行清单
    S.append(_sec("建议的下一步动作"))
    actions = _action_items(pc, diff)
    if actions:
        S.append(LIST(actions, ordered=True))
    else:
        S.append(P("当前没有需要立刻处理的问题。建议扩大样本量后再复测一次。"))

    # 10. 小样本诚实性
    S.append(_sec("这份报告的可信边界"))
    S.append(TABLE(_honesty_table(pc), caption="表 8 样本量与结论可信度"))
    S.append(CALLOUT(
        "样本量的单位是**玩家数（%d 人）**，不是记录行数（%d 行）。"
        "把行数当样本量会让置信区间假性收窄，结论看起来会比实际更可信。"
        % (pc.n_players, ds.n_rows), "info"))
    if pc.n_players < 8:
        S.append(CALLOUT(
            "**样本量低于 8 人门槛。** 本报告的所有结论都应视为方向性参考，"
            "不足以支撑直接调整线上数值。建议补到 8-10 人再复测。", "warn"))

    # 11. 数据体检
    S.append(_sec("数据体检"))
    if ds.issues:
        iss = pd.DataFrame([{"级别": {"error": "错误", "warn": "警告",
                                      "info": "提示"}.get(i["level"], i["level"]),
                             "说明": i["msg"]} for i in ds.issues])
        S.append(TABLE(iss, caption="表 9 导入时发现的问题"))
    if ds.unmapped:
        S.append(P("未识别的列：%s（已原样保留，未参与计算）" % "、".join(ds.unmapped)))
    if pc.level_metrics.skipped:
        sk = pd.DataFrame([{"指标": onto.metrics[m].name if m in onto.metrics else m,
                            "原因": reason} for m, reason in pc.level_metrics.skipped])
        S.append(TABLE(sk, caption="表 10 本次没能计算的指标（缺字段）"))
        S.append(P("补上这些字段，下次就能多算这些指标。"))

    # 12. 口径说明
    S.append(_sec("口径说明"))
    S.append(P("本节由 `metrics.yaml` 自动生成。**策划与数据分析以本节为准**——"
               "这是为了避免「同一个卡关占比，两个人算出两个数」这种情况。"))
    S.append(TABLE(_glossary_table(pc), caption="表 11 本次用到指标的口径"))

    if notes:
        S.append(H(2, "备注"))
        S.append(LIST(notes, ordered=False))

    headline = _headline_verdict(pc)
    md = render_markdown(title or "PlayPulse 难度体检报告", subtitle, S)
    html = render_html(title or "PlayPulse 难度体检报告", subtitle, S)
    # Excel 单独要一份纯数值宽表：报告里的「数值」列带了单位/量纲，是字符串，没法在表里再算
    wide = pc.level_metrics.wide.copy()
    wide.columns = [pc.level_metrics.meta[m]["name"]
                    if m in pc.level_metrics.meta else m for m in wide.columns]
    wide.index.name = "关卡"
    return Report(title=title or "PlayPulse 难度体检报告", subtitle=subtitle,
                  sections=S, markdown=md, html=html, generated_at=now,
                  meta={"n_players": pc.n_players, "n_findings": len(pc.findings),
                        "headline": headline, "wide": wide})


def _headline_verdict(pc: PulseCheck) -> str:
    """一句话总评。没有问题时也要说清楚，不能留空。"""
    highs = pc.findings_by_severity("high")
    if highs:
        f = highs[0]
        tail = ""
        if len(highs) > 1:
            tail = "；另有 %d 个高优先级问题" % (len(highs) - 1)
        return ("最需要处理的是**第 %s 关**：%s%s。"
                % (f.level if f.level is not None else "全局",
                   f.conclusion.replace("**", ""), tail))
    meds = pc.findings_by_severity("medium")
    if meds:
        return ("没有高优先级问题，但有 %d 个中等信号值得关注，其中：%s。"
                % (len(meds), meds[0].conclusion.replace("**", "")))
    return ("本次未检测到明确异常。注意：这既可能是真的没问题，"
            "也可能是样本量不足以触发判定（当前 %d 人）。" % pc.n_players)


#: 可以直接跟在数字后面的真单位。其余的一律当「取值域」拆成单独一列
_TRUE_UNITS = {"秒", "次", "分", "pt", "人", "%"}


# 逐关 × 逐指标明细表，数值与量纲分列
def _level_detail_table(pc: PulseCheck) -> pd.DataFrame:
    lm = pc.level_metrics
    rows = []
    for lv in sorted(lm.wide.index):
        for m in lm.meta.keys():
            d = lm.detail_at(m, int(lv))
            if not d or not np.isfinite(d.get("point", float("nan"))):
                continue
            name = lm.meta[m]["name"]
            unit = lm.meta[m]["unit"]
            lo, hi = d.get("ci_low"), d.get("ci_high")
            ci = ("[%.2f, %.2f]" % (lo, hi)) if np.isfinite(lo) and np.isfinite(hi) else "—"
            # unit 里混了两类：真单位（秒/次/%）能跟在数字后；取值域（1-5、0-100）
            # 跟在后面会拼成「1.191-5」这种读不懂的东西，所以拆成两列
            value, scale = ("%.2f%s" % (d["point"], unit), "") if unit in _TRUE_UNITS \
                else ("%.2f" % d["point"], unit)
            rows.append({
                "关卡": int(lv), "指标": name,
                "数值": value,
                "量纲": scale,
                "95% 置信区间": ci,
                "样本(人)": d["n"],
                "可信度": d.get("verdict_label", ""),
            })
    return pd.DataFrame(rows)


# 各指标样本量 vs 门槛的可信度表
def _honesty_table(pc: PulseCheck) -> pd.DataFrame:
    lm = pc.level_metrics
    rows = []
    for m, meta in lm.meta.items():
        ns = lm.long[lm.long["metric"] == m]["n"]
        if ns.empty:
            continue
        n = int(ns.max())
        min_n = int(meta["min_n"])
        v = statsx.verdict_by_sample_size(n, min_n)
        rows.append({
            "指标": meta["name"],
            "样本量": "%d 人" % n,
            "门槛": "%d 人" % min_n,
            "判定": statsx.VERDICT_LABEL[v],
        })
    return pd.DataFrame(rows)


# 本次实际用到的指标的口径说明表
def _glossary_table(pc: PulseCheck) -> pd.DataFrame:
    onto = pc.ontology
    rows = []
    used = set(pc.level_metrics.meta.keys())
    for m in onto.metrics.values():
        if m.id not in used:
            continue
        rows.append({
            "指标": m.name,
            "口径（公式）": "`%s`" % m.formula,
            "聚合": {"median": "中位数", "mean": "均值"}.get(m.agg, m.agg),
            "方向": {"higher_is_worse": "越高越差", "higher_is_better": "越高越好",
                     "neutral": "中性"}.get(m.direction, m.direction),
            "最低样本": "%d 人" % m.min_n,
            "注意": m.caveat or "—",
        })
    return pd.DataFrame(rows)


def _action_items(pc: PulseCheck, diff: Optional[VersionDiff]) -> List[str]:
    """把 findings 转成可执行清单（特色五：结论 → 建议 → 可导出）。"""
    out: List[str] = []
    seen = set()
    for f in pc.findings_sorted():
        if f.severity == "low" and len(out) >= 3:
            continue
        if not f.advice:
            continue
        key = f.advice[:30]
        if key in seen:
            continue
        seen.add(key)
        where = "第 %d 关" % f.level if f.level is not None else "全局"
        trust = "" if f.is_trustworthy else "（样本 %d 人，先验证再动手）" % f.n
        out.append("**%s · %s**：%s%s" % (where, f.name.split("：")[0], f.advice, trust))

    # 归因层面的动作
    if not pc.attribution_overall.empty:
        top = pc.attribution_overall.iloc[0]
        out.append("**归因层面**：%s 占 %.0f%%，修法方向是「%s」"
                   % (top["归因"], top["占比"] * 100, top["修法方向"]))

    if diff is not None:
        if diff.harder_levels:
            out.append("**版本回归**：第 %s 关在新版本变难，确认是否为预期改动"
                       % "、".join(map(str, diff.harder_levels)))
        if not diff.conclusions:
            out.append("**补样本后复测**：当前对比未检测到超出噪声的差异")

    if pc.n_players < 8:
        out.append("**补样本**：当前 %d 人，建议补到 8-10 人再定稿数值" % pc.n_players)
    return out


def render_markdown(title: str, subtitle: str, sections: List[dict]) -> str:
    """把中间结构渲染成 Markdown（表格手写，不依赖 tabulate）。"""
    out = ["# %s" % title, "", "> %s" % subtitle, ""]

    for s in sections:
        t = s["type"]
        if t == "heading":
            out += ["%s %s" % ("#" * s["level"], s["text"]), ""]
        elif t == "para":
            out += [s["text"], ""]
        elif t == "callout":
            out += ["> %s %s" % (_ICONS.get(s["kind"], ""), s["text"]), ""]
        elif t == "list":
            for i, item in enumerate(s["items"]):
                out.append("%d. %s" % (i + 1, item) if s["ordered"] else "- %s" % item)
            out.append("")
        elif t == "table":
            if s.get("caption"):
                out += ["**%s**" % s["caption"], ""]
            d = s["df"]
            if d is None or d.empty:
                out += ["_（无数据）_", ""]
                continue
            d = d.reset_index() if s.get("index", False) else d.copy()
            cols = list(d.columns)
            rows = ["| " + " | ".join(map(str, cols)) + " |",
                    "|" + "|".join(["---"] * len(cols)) + "|"]
            for _, r in d.iterrows():
                rows.append("| " + " | ".join(
                    "—" if isinstance(v, float) and np.isnan(v)
                    else str(v).replace("\n", " ").replace("|", "/") for v in r) + " |")
            out += ["\n".join(rows), ""]
        elif t == "image":
            out += ["_%s（图见 HTML 版）_" % s.get("caption", ""), ""]
        elif t == "raw":
            out += [s["text"], ""]
    return "\n".join(out)


CSS = """
:root{--fg:#1f2937;--muted:#6b7280;--line:#e5e7eb;--bg:#ffffff;--soft:#f9fafb;
--blue:#2563eb;--red:#dc2626;--amber:#f59e0b;--green:#059669;}
*{box-sizing:border-box}
body{margin:0;padding:0;background:#f3f4f6;color:var(--fg);
font-family:"Microsoft YaHei","PingFang SC","Helvetica Neue",Arial,sans-serif;
line-height:1.7;font-size:15px;}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px 64px;background:var(--bg);
box-shadow:0 1px 3px rgba(0,0,0,.08);}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.4px}
.sub{color:var(--muted);font-size:14px;margin:0 0 24px;padding-bottom:18px;border-bottom:2px solid var(--line)}
h2{font-size:19px;margin:34px 0 14px;padding-left:11px;border-left:4px solid var(--blue)}
h3{font-size:16px;margin:22px 0 10px}
p{margin:10px 0}
table{border-collapse:collapse;width:100%;margin:12px 0 18px;font-size:13.5px}
th{background:var(--soft);text-align:left;padding:8px 10px;border:1px solid var(--line);
font-weight:600;white-space:nowrap}
td{padding:7px 10px;border:1px solid var(--line);vertical-align:top}
tr:nth-child(even) td{background:#fcfcfd}
.cap{color:var(--muted);font-size:12.5px;margin:14px 0 2px;font-weight:600}
.callout{padding:12px 15px;margin:14px 0;border-radius:6px;border-left:4px solid var(--blue);
background:#eff6ff;font-size:14px}
.callout.warn{border-color:var(--amber);background:#fffbeb}
.callout.danger{border-color:var(--red);background:#fef2f2}
.callout.ok{border-color:var(--green);background:#ecfdf5}
.callout.info{border-color:var(--blue);background:#eff6ff}
figure{margin:18px 0}
figure img{max-width:100%;border:1px solid var(--line);border-radius:6px;background:#fff}
figcaption{color:var(--muted);font-size:12.5px;margin-top:6px;text-align:center}
ol,ul{padding-left:22px}
li{margin:7px 0}
code{background:#f3f4f6;padding:1px 5px;border-radius:3px;font-size:12.5px;
font-family:"Consolas","Monaco",monospace}
.foot{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);
color:var(--muted);font-size:12.5px;text-align:center}
strong{font-weight:600}
"""


# 把同一份中间结构渲染成单文件自包含 HTML
def render_html(title: str, subtitle: str, sections: List[dict]) -> str:
    body: List[str] = []
    for s in sections:
        t = s["type"]
        if t == "heading":
            body.append("<h%d>%s</h%d>" % (s["level"], _esc(s["text"]), s["level"]))
        elif t == "para":
            body.append("<p>%s</p>" % _md_inline(_esc(s["text"])))
        elif t == "callout":
            icon = _ICONS.get(s["kind"], "")
            body.append('<div class="callout %s">%s %s</div>'
                        % (s["kind"], icon, _md_inline(_esc(s["text"]))))
        elif t == "list":
            tag = "ol" if s["ordered"] else "ul"
            items = "".join("<li>%s</li>" % _md_inline(_esc(i)) for i in s["items"])
            body.append("<%s>%s</%s>" % (tag, items, tag))
        elif t == "table":
            if s.get("caption"):
                body.append('<div class="cap">%s</div>' % _esc(s["caption"]))
            d = s["df"]
            if d is None or d.empty:
                body.append("<p><em>（无数据）</em></p>")
                continue
            d = d.reset_index() if s.get("index", False) else d.copy()
            for c in d.columns:
                if d[c].dtype.kind == "f":
                    d[c] = d[c].map(lambda v: "—" if pd.isna(v) else "%.3f" % v)
            body.append(d.to_html(index=False, escape=False, border=0))
        elif t == "image":
            body.append('<figure><img src="data:image/png;base64,%s" alt="%s"/>'
                        '<figcaption>%s</figcaption></figure>'
                        % (s["b64"], _esc(s.get("caption", "")), _esc(s.get("caption", ""))))
        elif t == "raw":
            body.append(s["text"])

    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"/>"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>"
            "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">"
            "<h1>%s</h1><div class=\"sub\">%s</div>%s"
            "<div class=\"foot\">由 PlayPulse · 把脉 生成 · 指标口径以 metrics.yaml 为准</div>"
            "</div></body></html>"
            % (_esc(title), CSS, _esc(title), _esc(subtitle), "".join(body)))
