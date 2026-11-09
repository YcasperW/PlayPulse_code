"""Streamlit 多页共用：样式、上传、缓存与发现卡片渲染。"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEMO_CSV = os.path.join(ROOT, "data", "raw", "试玩数据-合成-100份.csv")

CSS = """
<style>
  .pp-hero { padding: 1.1rem 1.3rem; border-radius: 12px;
             background: linear-gradient(135deg, #16233a 0%, #1d2b45 100%);
             border: 1px solid #2b3d5c; margin-bottom: 1rem; }
  .pp-hero h1 { margin: 0 0 .3rem 0; font-size: 1.5rem; color: #e8eef8; }
  .pp-hero p  { margin: 0; color: #9fb0c9; font-size: .92rem; line-height: 1.6; }
  .pp-card { padding: .9rem 1rem; border-radius: 10px; background: #141d2e;
             border: 1px solid #26344d; margin-bottom: .7rem; }
  .pp-sev-high   { border-left: 4px solid #e5484d; }
  .pp-sev-medium { border-left: 4px solid #f5a524; }
  .pp-sev-low    { border-left: 4px solid #30a46c; }
  .pp-muted { color: #8b9bb4; font-size: .85rem; }
  .pp-kpi { font-size: 1.5rem; font-weight: 600; color: #e8eef8; }
</style>
"""


def inject_style() -> None:
    """把自定义样式塞进页面（每个页面都要调一次，多页之间不共享）。"""
    st.markdown(CSS, unsafe_allow_html=True)


def save_uploads(files) -> str:
    """把上传的文件落到临时目录，返回目录路径。"""
    tmp = tempfile.mkdtemp(prefix="playpulse_")
    for f in files:
        with open(os.path.join(tmp, f.name), "wb") as out:
            out.write(f.getbuffer())
    return tmp


def sidebar_data_source() -> Optional[str]:
    """侧边栏：上传 / 用内置样例 / 填项目名。返回数据路径，没有数据返回 None。"""
    with st.sidebar:
        st.header("数据")
        files = st.file_uploader("CSV / Excel，可多选（多份填写表会自动合并）",
                                 type=["csv", "xlsx", "xls"],
                                 accept_multiple_files=True, key="pp_uploader")
        if st.button("用内置合成样例（100 人 × 3 关）", key="pp_demo"):
            st.session_state["pp_path"] = DEMO_CSV
            st.session_state.pop("report_html", None)
            st.session_state.pop("report_xlsx", None)

        if files:
            st.session_state["pp_path"] = save_uploads(files)
            st.session_state.pop("report_html", None)
            st.session_state.pop("report_xlsx", None)

        if "pp_project" not in st.session_state:
            st.session_state["pp_project"] = ""
        st.text_input("项目名（存基准库 / 比基准线时用）",
                      placeholder="例如：开箱物语", key="pp_project")

        path = st.session_state.get("pp_path")
        if path:
            st.caption("当前数据：%s" % os.path.basename(path))
        return path


@st.cache_resource(show_spinner=False)
def cached_ontology():
    """指标本体解析一次就够，YAML 不必每次交互都读一遍。"""
    from playpulse.semantics import default_ontology
    return default_ontology()


@st.cache_data(show_spinner=False)
def cached_dataset(path: str):
    """按路径缓存规范化结果。"""
    from playpulse.io_load import load_auto
    return load_auto(path)


@st.cache_data(show_spinner=False)
def cached_pulse_check(path: str):
    """按路径缓存体检结果，数据集一并返回（页面要显示关卡数、版本数）。"""
    from playpulse.insights import run_pulse_check
    ds = cached_dataset(path)
    return run_pulse_check(ds, cached_ontology()), ds


def load_context() -> Optional[Tuple]:
    """取当前数据的 (PulseCheck, Dataset)；没数据或读失败就提示并返回 None。"""
    path = st.session_state.get("pp_path")
    if not path:
        st.info("👈 先在左侧上传一份试玩数据，或点「用内置合成样例」。")
        return None
    try:
        return cached_pulse_check(path)
    except Exception as e:
        st.error("数据读取或分析失败：%s" % e)
        st.code(str(e))
        return None


def render_finding(f) -> None:
    """渲染单条发现：严重度、结论、怎么改、样本量。"""
    sev = {"high": "pp-sev-high", "medium": "pp-sev-medium",
           "low": "pp-sev-low"}.get(f.severity, "pp-sev-low")
    badge = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(f.severity, f.severity)
    st.markdown('<div class="pp-card %s">' % sev, unsafe_allow_html=True)
    c1, c2 = st.columns([6, 1])
    with c1:
        st.markdown("**%s**　`%s`" % (f.name, badge))
    with c2:
        st.caption(f.verdict_label)
    st.markdown(f.conclusion)
    if f.advice:
        st.markdown("👉 **怎么改：** %s" % f.advice)
    st.caption("样本 %d 人" % f.n)
    st.markdown("</div>", unsafe_allow_html=True)


def render_issues(ds) -> None:
    """数据体检清单（导入时发现的问题）。"""
    if ds.is_synthetic:
        st.warning("本批数据包含**合成（仿真）数据**，结论只能用于验证链路，不能当真实玩家结论。")
    if ds.issues:
        with st.expander("数据体检：导入时发现 %d 个问题" % len(ds.issues), expanded=False):
            st.dataframe(pd.DataFrame([
                {"级别": {"error": "错误", "warn": "警告", "info": "提示"}.get(
                    i["level"], i["level"]),
                 "说明": i["msg"]} for i in ds.issues]),
                hide_index=True, width="stretch")


def project_name() -> str:
    """侧边栏填的项目名，没填就是空串（空串意味着不碰基准库）。"""
    return (st.session_state.get("pp_project") or "").strip()
