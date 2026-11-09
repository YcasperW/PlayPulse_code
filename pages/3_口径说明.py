"""口径说明页：展示 metrics.yaml 中的指标、阈值与规则。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui_common import inject_style, sidebar_data_source, cached_ontology

st.set_page_config(page_title="口径说明 · PlayPulse", page_icon="📖", layout="wide")
inject_style()
st.title("📖 口径说明")

sidebar_data_source()
onto = cached_ontology()

st.markdown("这份表由 `playpulse/semantics/metrics.yaml` 自动生成。"
            "**策划与数据分析以本节为准**——这是为了避免"
            "「同一个卡关占比，两个人算出两个数」。")

st.dataframe(pd.DataFrame([{
    "指标": m.name,
    "层级": m.layer,
    "口径（公式）": m.formula,
    "聚合": m.agg,
    "单位": m.unit or "—",
    "最少样本": m.min_n,
    "为什么这么算": (m.why or "").replace("\n", " "),
} for m in onto.metrics.values()]), hide_index=True, width="stretch")

st.markdown("#### 复合指标")
if getattr(onto, "composites", None):
    st.dataframe(pd.DataFrame([{
        "指标": c.name,
        "构成": " + ".join("%s×%g" % (x.metric, x.weight) for x in c.components),
        "为什么这么算": (c.why or "").replace("\n", " "),
    } for c in onto.composites.values()]), hide_index=True, width="stretch")

st.markdown("#### 判定阈值")
st.dataframe(pd.DataFrame([{"阈值": k, "取值": v} for k, v in onto.thresholds.items()]),
             hide_index=True, width="stretch")

st.markdown("#### 判定规则")
if getattr(onto, "rules", None):
    st.dataframe(pd.DataFrame([{
        "规则": r.name,
        "看什么": r.on,
        "阈值": r.threshold,
        "严重度": r.severity,
        "为什么": (r.why or "").replace("\n", " "),
    } for r in onto.rules.values()]), hide_index=True, width="stretch")

st.markdown("---")
st.download_button("⬇️ 下载完整口径说明（Markdown）",
                   data=onto.to_markdown().encode("utf-8"),
                   file_name="口径说明.md", mime="text/markdown")
st.caption("改口径 = 改 YAML + 走 review。通用层改动要评估对跨项目基准线的影响。")
