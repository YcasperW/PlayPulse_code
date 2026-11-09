"""PlayBench 页：查看基准线、存档体检结果、比对当前项目。"""

from __future__ import annotations

import streamlit as st

from ui_common import inject_style, sidebar_data_source, load_context, project_name

st.set_page_config(page_title="基准线 · PlayBench", page_icon="📈", layout="wide")
inject_style()
st.title("📈 PlayBench · 跨项目基准线")

st.caption("一个项目一票：每个项目只取最近一次体检，测了很多次的项目不会把基准拽过去。"
           "只比通用层指标——品类层指标跨项目比没有意义。")

sidebar_data_source()

from playpulse.bench import (build_baseline, baseline_table_for_display,  # noqa: E402
                             compare_to_baseline, save_run, list_projects,
                             MIN_PROJECTS, DEFAULT_METRICS, bench_root)

tab_lib, tab_line, tab_cmp = st.tabs(["🗂 基准库", "📊 基准线本体", "🔍 我 vs 基准线"])

with tab_lib:
    st.caption("基准库存放在 `%s`，一个项目一个目录。" % bench_root())
    t = list_projects()
    if t.empty:
        st.info("基准库还是空的。在左边上传数据 + 填项目名，然后到「我 vs 基准线」里点存档。")
    else:
        st.dataframe(t, hide_index=True, width="stretch")
        st.markdown("已入库 %d 个项目，基准线门槛是 %d 个。"
                    % (len(t), MIN_PROJECTS))

with tab_line:
    show_all = st.checkbox("显示全部通用层指标（默认只显示 7 个核心指标）", value=False)
    b = build_baseline(metrics=None if show_all else list(DEFAULT_METRICS))
    if b.table.empty:
        st.info("还没有能算基准线的记录。")
    else:
        if b.n_projects < MIN_PROJECTS:
            st.warning("当前只有 %d 个项目，不足 %d 个门槛——下面这些数字只能看趋势，"
                       "不能当成基准用。" % (b.n_projects, MIN_PROJECTS))
        st.dataframe(baseline_table_for_display(b), hide_index=True, width="stretch")
        st.caption("P25–P75 是同类的正常区间；每一格的样本量（n_projects）单独报，"
                   "因为各项目关卡数不一样。")

with tab_cmp:
    ctx = load_context()
    if ctx is None:
        st.stop()
    pc, ds = ctx

    proj = project_name()
    if not proj:
        st.info("在左边填上项目名再做对比：算基准线时要把自己排除掉，不然是自己跟自己比。")
        proj = st.text_input("临时填一个项目名也行", key="bench_tmp_project")
        if not proj:
            st.stop()

    cmp = compare_to_baseline(pc, project=proj)
    st.markdown("### %s" % cmp.verdict_sentence())
    for w in cmp.warnings:
        st.warning("⚠️ " + w)

    if cmp.table.empty:
        st.info("没有可对上的通用层指标（缺字段，或关卡序号对不上）。")
    else:
        show = cmp.table[["指标", "关卡", "本次", "基准中位数", "P25", "P75",
                          "差%", "位置", "项目数", "基准可信"]].copy()
        show["基准可信"] = show["基准可信"].map(lambda b: "是" if b else "否")
        st.dataframe(show, hide_index=True, width="stretch")

        hit = cmp.table[(cmp.table["值得关注"]) & (cmp.table["基准可信"])]
        if not hit.empty:
            st.markdown("#### 值得看的几条")
            for s in cmp.conclusions:
                st.markdown("- %s" % s)

    st.markdown("---")
    note = st.text_input("这次测试的备注（会一起存进记录）", key="bench_note",
                         placeholder="例如：第 2 轮内部试玩，调了第 2 关产出")
    if st.button("把这次体检存进基准库", type="primary"):
        p = save_run(pc, proj, note=note or "")
        st.success("已存入：%s" % p)
        st.caption("存的是通用层指标的点估计和判定结果，原始行不落库。")

with st.expander("基准线是怎么算的"):
    st.markdown("""
1. 每次体检结束后，把**通用层指标**的点估计存进这个项目自己的目录。
2. 算基准线时，每个项目取最近一条记录，按（指标 × 关卡序号）对齐，算 P25 / P50 / P75 / P90。
3. 跟基准线比的时候，当前项目自己会被排除，避免自己跟自己比。
4. 项目数不到 %d 个就不给基准线，只回一句"还攒不够"——
   跟小样本拒绝下结论是同一套逻辑。
5. 偏离中位数不到 10%% 一律算持平；各项目取值完全一样时直接说"比不出高低"，
   不会硬给一个"你高于同类"的结论。
""" % MIN_PROJECTS)
