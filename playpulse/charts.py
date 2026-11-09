"""图表：为报告 HTML 与 Web UI 生成同一套图。"""

from __future__ import annotations

import io
import base64
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


_FONT_READY = False


def setup_chinese_font():
    """解决 matplotlib 中文方块字问题（Windows 上最常见）。字体设一次就够。"""
    global _FONT_READY
    import matplotlib
    matplotlib.use("Agg")            # 无界面环境必须显式指定后端
    import matplotlib.pyplot as plt
    if not _FONT_READY:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                                           "Arial Unicode MS", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        plt.rcParams["figure.dpi"] = 110
        plt.rcParams["savefig.dpi"] = 110
        plt.rcParams["font.size"] = 10
        _FONT_READY = True
    return plt


def fig_to_base64(fig) -> str:
    """把图转成 base64 字符串，用于内联进 HTML。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode("ascii")
    setup_chinese_font().close(fig)
    return data


# 每张图的最后一步都一样：先紧凑排版，再转 base64
def _finish(fig) -> str:
    fig.tight_layout()
    return fig_to_base64(fig)


def fig_difficulty_curve(lm, onto=None) -> Optional[str]:
    """难度曲线：耗时中位数（带置信区间）+ 难度指数。"""
    if lm.wide is None or lm.wide.empty:
        return None
    plt = setup_chinese_font()

    levels = sorted(lm.wide.index)
    dur = [lm.value_at("duration_sec", lv) for lv in levels]
    lo = [lm.detail_at("duration_sec", lv).get("ci_low", float("nan")) for lv in levels]
    hi = [lm.detail_at("duration_sec", lv).get("ci_high", float("nan")) for lv in levels]
    idx = ([lm.value_at("difficulty_index", lv) for lv in levels]
           if "difficulty_index" in lm.wide.columns else None)

    fig, ax = plt.subplots(figsize=(8, 3.6))
    x = np.array(levels, dtype=float)

    ax.plot(x, dur, "-o", color="#2563eb", linewidth=2, markersize=6, label="关卡耗时（中位数）")
    ax.fill_between(x, lo, hi, color="#2563eb", alpha=0.15, label="95% 置信区间")
    ax.set_ylabel("耗时（秒）", color="#2563eb")
    ax.tick_params(axis="y", labelcolor="#2563eb")
    ax.set_xlabel("关卡")
    ax.set_xticks(levels)
    ax.grid(axis="y", alpha=0.25, linestyle="--")

    # 断崖标注
    for i in range(1, len(levels)):
        if dur[i - 1] > 0 and np.isfinite(dur[i]) and np.isfinite(dur[i - 1]):
            r = dur[i] / dur[i - 1]
            if r >= 1.5:
                ax.annotate("断崖 %.2fx" % r, xy=(x[i], dur[i]),
                            xytext=(0, 12), textcoords="offset points",
                            ha="center", fontsize=9, color="#dc2626", fontweight="bold",
                            arrowprops=dict(arrowstyle="->", color="#dc2626", lw=1.2))

    if idx is not None and all(np.isfinite(v) for v in idx):
        ax2 = ax.twinx()
        ax2.plot(x, idx, "--s", color="#f59e0b", linewidth=1.8, markersize=5,
                 alpha=0.85, label="难度指数")
        ax2.set_ylabel("难度指数（0-100）", color="#f59e0b")
        ax2.tick_params(axis="y", labelcolor="#f59e0b")
        ax2.set_ylim(0, max(idx) * 1.25 if max(idx) else 100)

    ax.set_title("难度曲线（阴影为 95% 置信区间）", fontsize=12, pad=10)
    lines1, lab1 = ax.get_legend_handles_labels()
    ax.legend(lines1, lab1, loc="upper left", fontsize=8, framealpha=0.9)
    return _finish(fig)


def fig_quadrants(q: pd.DataFrame) -> Optional[str]:
    """客观难度 × 感知难度 四象限散点。"""
    if q is None or q.empty:
        return None
    plt = setup_chinese_font()
    fig, ax = plt.subplots(figsize=(5.6, 4.6))

    colors = {"真实卡点": "#dc2626", "假性难度": "#f59e0b",
              "顺滑但可能无聊": "#6b7280", "心流/无感消耗": "#059669"}
    for name, g in q.groupby("象限"):
        ax.scatter(g["客观难度"], g["感知难度"], s=160, alpha=0.85,
                   color=colors.get(name, "#2563eb"), label=name, edgecolors="white", linewidth=1.5)
        for _, r in g.iterrows():
            ax.annotate("L%d" % int(r["关卡"]), (r["客观难度"], r["感知难度"]),
                        textcoords="offset points", xytext=(9, -3), fontsize=9, fontweight="bold")

    ax.axvline(q["客观难度"].median(), color="#9ca3af", linestyle="--", linewidth=1, alpha=0.7)
    ax.axhline(q["感知难度"].median(), color="#9ca3af", linestyle="--", linewidth=1, alpha=0.7)
    ax.set_xlabel("客观难度（难度指数）")
    ax.set_ylabel("感知难度（逐关累加）")
    ax.set_title("客观 × 感知 四象限", fontsize=12, pad=10)
    ax.grid(alpha=0.2, linestyle="--")
    ax.legend(fontsize=8, loc="best", framealpha=0.9)
    return _finish(fig)


def fig_distribution(df: pd.DataFrame, label_col: str, value_col: str = "占比",
                     title: str = "", color: str = "#2563eb",
                     extra_col: Optional[str] = None) -> Optional[str]:
    """通用横向条形图（归因分布 / 选择分布）。"""
    if df is None or df.empty:
        return None
    plt = setup_chinese_font()
    d = df.sort_values(value_col, ascending=True)
    fig, ax = plt.subplots(figsize=(6.4, max(2.2, 0.5 * len(d) + 1.2)))
    y = np.arange(len(d))
    vals = d[value_col].astype(float) * 100
    ax.barh(y, vals, color=color, alpha=0.85, height=0.62)
    for i, v in enumerate(vals):
        ax.text(v + 1, i, "%.0f%%" % v, va="center", fontsize=9, color="#374151")
    ax.set_yticks(y)
    ax.set_yticklabels(d[label_col].astype(str))
    ax.set_xlabel("占比（%）")
    ax.set_xlim(0, max(vals) * 1.22 if len(vals) else 100)
    ax.set_title(title, fontsize=12, pad=10)
    ax.grid(axis="x", alpha=0.2, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return _finish(fig)


def fig_version_diff(vd) -> Optional[str]:
    """版本对比：难度曲线前后叠图 + 逐关变化量。"""
    if not vd or not vd.difficulty_curve:
        return None
    plt = setup_chinese_font()
    curve = vd.difficulty_curve
    fig, ax = plt.subplots(figsize=(7.2, 3.6))

    for i, (tag, vals) in enumerate(curve.items()):
        x = np.arange(1, len(vals) + 1)
        ax.plot(x, vals, "-o", linewidth=2, markersize=6,
                label=tag, color=["#2563eb", "#f59e0b"][i % 2])

    for lv in vd.harder_levels:
        ax.axvline(lv, color="#dc2626", alpha=0.25, linestyle="--", linewidth=6)
    for lv in vd.easier_levels:
        ax.axvline(lv, color="#059669", alpha=0.25, linestyle="--", linewidth=6)

    ax.set_xticks(np.arange(1, len(next(iter(curve.values()))) + 1))
    ax.set_xlabel("关卡")
    ax.set_ylabel("难度指数 / 耗时")
    ax.set_title("版本对比：%s → %s（红=变难，绿=变易）" % (vd.ver_a, vd.ver_b),
                 fontsize=12, pad=10)
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    ax.legend(fontsize=9, framealpha=0.9)
    return _finish(fig)


def fig_metric_bars(lm, metric: str, title: Optional[str] = None,
                    threshold: Optional[float] = None) -> Optional[str]:
    """单指标的逐关柱状图，可带阈值线。"""
    if lm.wide is None or metric not in lm.wide.columns:
        return None
    plt = setup_chinese_font()
    levels = sorted(lm.wide.index)
    vals = [lm.value_at(metric, lv) for lv in levels]
    err_lo = [max(0, lm.value_at(metric, lv) - (lm.detail_at(metric, lv).get("ci_low") or 0))
              for lv in levels]
    err_hi = [max(0, (lm.detail_at(metric, lv).get("ci_high") or 0) - lm.value_at(metric, lv))
              for lv in levels]

    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    x = np.arange(len(levels))
    ax.bar(x, vals, color="#2563eb", alpha=0.85, width=0.55,
           yerr=[err_lo, err_hi], capsize=4, error_kw=dict(ecolor="#6b7280", lw=1))
    if threshold is not None:
        ax.axhline(threshold, color="#dc2626", linestyle="--", linewidth=1.5,
                   label="阈值 %.2f" % threshold)
        ax.legend(fontsize=8, framealpha=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels(["第%d关" % lv for lv in levels])
    ax.set_ylabel(metric)
    ax.set_title(title or metric, fontsize=12, pad=10)
    ax.grid(axis="y", alpha=0.2, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    return _finish(fig)
