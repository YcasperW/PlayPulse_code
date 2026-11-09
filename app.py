"""PlayPulse Web UI 首页：上传数据即生成报告。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui_common import (inject_style, sidebar_data_source, load_context,
                       render_finding, render_issues, project_name)

st.set_page_config(page_title="PlayPulse · 把脉", page_icon="🩺", layout="wide")
inject_style()

st.markdown("""
<div class="pp-hero">
  <h1>🩺 PlayPulse · 把脉</h1>
  <p>上传一份试玩数据，先给你一份难度体检报告——<b>不用你提问</b>。<br>
     样本不够的时候，它会直接说「这个结论还不能信」，不会硬给一个结论。</p>
</div>
""", unsafe_allow_html=True)

path = sidebar_data_source()

if not path:
    st.markdown("""
**这份工具会给你什么**

1. **难度体检报告** —— 上传即出。哪几关在赶人、为什么赶人、该怎么改。
2. **版本对比 Diff** —— 改完一版数值，是变难了还是变简单了，用效应量 + 置信区间说话，
   并且会提醒你「同一批玩家第二次玩会更快」这种学习效应。
3. **PlayBench 基准线** —— 跟同类项目比：你第 2 关 39 秒，同类一般 39 秒，最陡的那档是 41.5 秒。
4. **口径文档** —— 每个指标的定义、公式、阈值都来自一份 YAML，可 review、可 diff、可导出。

**它不会给你什么**

- 不会生成 SQL 去查你的库（决策 D1）。口径是预先定死的，不是临场编的。
- 不会在样本不够的时候假装够。8 人以下只给方向，5 人以下拒绝判断。
- 不会在基准线只有 1、2 个项目时假装那是「行业基准」。
""")
    st.stop()

ctx = load_context()
if ctx is None:
    st.stop()
pc, ds = ctx

# 顶部 KPI
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown('<div class="pp-kpi">%d</div><div class="pp-muted">玩家数（样本量单位）</div>'
                % pc.n_players, unsafe_allow_html=True)
with k2:
    st.markdown('<div class="pp-kpi">%d</div><div class="pp-muted">关卡数</div>'
                % len(ds.levels), unsafe_allow_html=True)
with k3:
    st.markdown('<div class="pp-kpi">%d</div><div class="pp-muted">高危问题</div>'
                % len(pc.findings_by_severity("high")), unsafe_allow_html=True)
with k4:
    ok = sum(1 for f in pc.findings if f.is_trustworthy)
    st.markdown('<div class="pp-kpi">%d / %d</div><div class="pp-muted">可信结论 / 总结论</div>'
                % (ok, len(pc.findings)), unsafe_allow_html=True)

render_issues(ds)
st.markdown("---")

tab_report, tab_findings, tab_levels = st.tabs(["📄 体检报告", "🔍 关键发现", "📊 逐关明细"])

with tab_report:
    do_diff = st.checkbox("报告里附上版本对比", value=False, key="home_diff")
    va = vb = ""
    if do_diff and len(ds.versions) >= 2:
        ca, cb = st.columns(2)
        with ca:
            va = st.selectbox("基线版本（旧）", ds.versions, index=0, key="home_va")
        with cb:
            vb = st.selectbox("对照版本（新）", ds.versions,
                              index=min(1, len(ds.versions) - 1), key="home_vb")
    elif do_diff:
        st.info("数据里只有一个版本（%s），没法对比。" % "、".join(map(str, ds.versions)))

    proj = project_name()
    sig = (path, proj, do_diff, va, vb)
    if st.session_state.get("report_sig") != sig:
        with st.spinner("正在把脉…"):
            from playpulse.report import build_report
            from playpulse.diff import version_diff
            from playpulse.bench import compare_to_baseline
            from playpulse.export_excel import excel_bytes

            diff_obj = None
            if do_diff and va and vb and va != vb:
                try:
                    diff_obj = version_diff(ds, pc.ontology, va, vb)
                except Exception as e:
                    st.error("版本对比失败：%s" % e)
            bench_obj = compare_to_baseline(pc, project=proj or None) if proj else None
            rep = build_report(pc, diff=diff_obj, bench=bench_obj)
        st.session_state["report_sig"] = sig
        st.session_state["report_html"] = rep.html
        st.session_state["report_md"] = rep.markdown
        st.session_state["report_xlsx"] = excel_bytes(rep)

    html = st.session_state.get("report_html", "")
    if not html:
        st.warning("报告没生成出来。")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button("⬇️ 下载 HTML 报告（自包含，可直接发给别人）",
                               data=html.encode("utf-8"),
                               file_name="难度体检报告.html", mime="text/html")
        with c2:
            st.download_button("⬇️ 下载 Markdown 报告",
                               data=st.session_state["report_md"].encode("utf-8"),
                               file_name="难度体检报告.md", mime="text/markdown")
        with c3:
            if st.session_state.get("report_xlsx"):
                st.download_button(
                    "⬇️ 下载 Excel 报告（每张表一个 sheet）",
                    data=st.session_state["report_xlsx"],
                    file_name="难度体检报告.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.iframe(html, height=1600)

with tab_findings:
    if not pc.findings:
        st.success("本次没有检测到明确异常。注意这既可能是真的没问题，"
                   "也可能是样本量不足以触发判定（当前 %d 人）。" % pc.n_players)
    for f in pc.findings_sorted():
        render_finding(f)

with tab_levels:
    lm = pc.level_metrics
    metric_ids = [m for m in lm.meta if m in lm.wide.columns]
    pick = st.multiselect("选择要看的指标", metric_ids,
                          default=[m for m in ("difficulty_index", "duration_sec",
                                               "stuck_ratio", "attempts",
                                               "time_dilation") if m in metric_ids])
    if pick:
        st.dataframe(lm.wide[pick].round(2), width="stretch")
        st.caption("表内为点估计（中位数或均值，取决于指标定义）。置信区间见下方明细。")
    with st.expander("完整明细（含 95% 置信区间与可信度判定）"):
        st.dataframe(lm.long, hide_index=True, width="stretch")
    if lm.skipped:
        st.warning("以下指标本次算不了（缺字段），没有默默跳过：")
        st.dataframe(pd.DataFrame(
            [{"指标": pc.ontology.metrics[m].name if m in pc.ontology.metrics else m,
              "原因": why} for m, why in lm.skipped]), hide_index=True)

st.markdown("---")
st.caption("PlayPulse · 把脉　|　口径单一来源：playpulse/semantics/metrics.yaml")
