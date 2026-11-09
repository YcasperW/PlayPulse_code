"""追问页：把问题路由到预定义分析卡片。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui_common import inject_style, sidebar_data_source, load_context
from playpulse import cards as C

st.set_page_config(page_title="追问 · PlayPulse", page_icon="💬", layout="wide")
inject_style()
st.title("💬 追问")

st.caption("你说一句话，系统路由到一张预先定义好口径的分析卡片。"
           "**这里不生成 SQL**——口径是预先定死的，不是临场编的。")

sidebar_data_source()
ctx = load_context()
if ctx is None:
    st.stop()
pc, ds = ctx

with st.expander("有哪些可以问的？"):
    st.dataframe(pd.DataFrame(
        [{"问题": c.question, "卡片": c.name, "口径依据": c.why} for c in C.CARDS]),
        hide_index=True, width="stretch")

q = st.text_input("问一句人话，例如：哪关最难？")
if q:
    res = C.ask(pc, q)
    if res.matched:
        st.markdown("### %s" % res.headline)
        if res.table is not None and not res.table.empty:
            st.dataframe(res.table, hide_index=True, width="stretch")
    else:
        st.warning(res.headline)
    for n in res.notes:
        if n.strip():
            st.markdown("- " + n)
