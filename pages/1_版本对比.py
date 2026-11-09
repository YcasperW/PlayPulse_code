"""版本对比页：按关卡对齐两版数据并输出差异结论。"""

from __future__ import annotations

import streamlit as st

from ui_common import inject_style, sidebar_data_source, load_context

st.set_page_config(page_title="版本对比 · PlayPulse", page_icon="🔁", layout="wide")
inject_style()
st.title("🔁 版本对比")

sidebar_data_source()
ctx = load_context()
if ctx is None:
    st.stop()
pc, ds = ctx

if len(ds.versions) < 2:
    st.info("当前数据只有一个版本（%s）。版本对比需要数据里含多个 build_version——"
            "调完数值记得换版本号，否则事后没法证明改版是变好还是变差。"
            % "、".join(map(str, ds.versions)))
    st.stop()

from playpulse.diff import version_diff  # noqa: E402

ca, cb = st.columns(2)
with ca:
    va = st.selectbox("基线（旧）", ds.versions, index=0, key="dva")
with cb:
    vb = st.selectbox("对照（新）", ds.versions,
                      index=min(1, len(ds.versions) - 1), key="dvb")

if va == vb:
    st.warning("两个版本选得一样，换一个再比。")
    st.stop()

try:
    vd = version_diff(ds, pc.ontology, va, vb)
except Exception as e:
    st.error("对比失败：%s" % e)
    st.stop()

st.markdown("### %s" % vd.verdict_sentence())
st.caption("基线 %d 人 / 对照 %d 人" % (vd.n_a, vd.n_b))

for w in vd.warnings:
    st.warning("⚠️ " + w)

c1, c2 = st.columns(2)
with c1:
    if vd.harder_levels:
        st.error("变难的关卡：%s" % "、".join(map(str, vd.harder_levels)))
with c2:
    if vd.easier_levels:
        st.success("变简单的关卡：%s" % "、".join(map(str, vd.easier_levels)))

st.markdown("#### 逐关逐指标")
st.dataframe(vd.table, hide_index=True, width="stretch")

st.markdown("#### 整体对比")
st.dataframe(vd.overall, hide_index=True, width="stretch")

with st.expander("怎么读这张表"):
    st.markdown("""
- **超出噪声** 那一列是判断的关键：差值的置信区间如果跨过 0，
  就意味着「这个变化可能只是随机波动」，不要写进汇报。
- 效应量 Cohen's d 说的是"差多少"，p 值说的是"这个差别是不是巧合"。
  两个都要看，只看 p 值会在大样本上把微小差异当成重大发现。
- 学习效应警告不是报错：同一批玩家第二次玩本来就会更快，
  这时候测出来的"变简单了"有一部分是他们变熟了，不是你改得好。
""")
