"""Excel 渲染器：把报告 sections 写成 xlsx。"""

from __future__ import annotations

import io
import os
import re
import unicodedata
from typing import List, Set

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# 报告里的 markdown 标记在 Excel 里是噪音，写单元格前剥掉
_plain = lambda s: re.sub(r"`(.+?)`", r"\1",
                          re.sub(r"\*\*(.+?)\*\*", r"\1", str(s)))
# callout 底色跟 HTML 版保持一致
_FILL = {"info": "EFF6FF", "warn": "FFFBEB", "danger": "FEF2F2", "ok": "ECFDF5"}
_LINE = Side(style="thin", color="E5E7EB")
_BORDER = Border(left=_LINE, right=_LINE, top=_LINE, bottom=_LINE)
_HEAD = PatternFill("solid", fgColor="F3F4F6")
_CN_FONT = "Microsoft YaHei"


# 单元格值清洗：NaN / NaT / numpy 标量 openpyxl 不认，统一转成能写的类型
def _clean(v):
    if v is None:
        return "—"
    try:
        if not isinstance(v, (list, tuple, dict, np.ndarray)) and pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool, np.bool_)):
        return "是" if bool(v) else "否"
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return "—" if not np.isfinite(f) else f
    # 以 = 开头会被 Excel 当公式，加个引号保住原文
    s = str(v).replace("\n", " ")
    return "'" + s if s.startswith("=") else s


# 显示宽度：中文按两格算，列宽才不会挤成一团
def _width(s) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in str(s or ""))


# 每张表一个 sheet：去掉「表 N」前缀、避开 Excel 的非法字符和 31 字上限
def _sheet_name(caption: str, used: Set[str]) -> str:
    name = re.sub(r"^表\s*[·:：]\s*", "", caption or "")
    name = re.sub(r"^表\s*\d+\s*", "", name)
    name = re.sub(r"^表\s*", "", name).strip()
    name = re.sub(r"[\\/*?:\[\]]", "-", name).strip("'")[:31] or "数据表"
    base, i = name, 2
    while name in used:
        tail = "_%d" % i
        name = base[:31 - len(tail)] + tail
        i += 1
    used.add(name)
    return name


# 列宽按每列最长内容自适应，上限 60 防止某段长文本把表撑爆
def _fit(ws) -> None:
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        w = max((_width(c.value) for c in col), default=8)
        ws.column_dimensions[letter].width = min(max(w + 3, 11), 60)


# 报告说明页：按原顺序铺标题、段落、提示、清单；表格和图只留指引
def _write_text_sheet(ws, report, table_names: List[str], extra: str = "") -> None:
    ws.append([report.title])
    ws["A1"].font = Font(name=_CN_FONT, size=15, bold=True, color="1F2937")
    ws.append([report.subtitle])
    ws.append(["生成时间：%s" % report.generated_at])
    ws.append(["本文件由 PlayPulse 自动生成；指标口径以 playpulse/semantics/metrics.yaml 为准。"])
    ws.append([])

    ti = 0
    for s in report.sections:
        t = s["type"]
        if t == "heading":
            row = ["  " * max(0, s["level"] - 2) + _plain(s["text"])]
            ws.append(row)
            ws.cell(ws.max_row, 1).font = Font(name=_CN_FONT, bold=True, size=13)
        elif t == "para":
            ws.append([_plain(s["text"])])
        elif t == "callout":
            ws.append([_plain(s["text"])])
            c = ws.cell(ws.max_row, 1)
            c.fill = PatternFill("solid", fgColor=_FILL.get(s["kind"], "EFF6FF"))
            c.alignment = Alignment(wrap_text=True, vertical="top")
        elif t == "list":
            for i, item in enumerate(s["items"]):
                ws.append([("%d. " % (i + 1) if s["ordered"] else "· ") + _plain(item)])
        elif t == "table":
            cap = s.get("caption") or "数据表"
            name = table_names[ti] if ti < len(table_names) else "数据表"
            ti += 1
            ws.append(["→ %s：见工作表「%s」" % (cap, name)])
        elif t == "image":
            ws.append(["（%s，图见 HTML 版）" % (s.get("caption") or "图表")])
        elif t == "raw":
            ws.append([_plain(s["text"])])

    if extra:
        ws.append([])
        ws.append([extra])
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=1):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    _fit(ws)


# 数据页：第一行 caption、第二行表头，之后每行数据；带筛选和冻结
def _write_data_sheet(ws, df, caption: str) -> None:
    d = df.reset_index() if df.index.name is not None else df.copy()
    ws.append([caption or "数据表"])
    ws["A1"].font = Font(name=_CN_FONT, size=13, bold=True, color="1F2937")
    cols = [str(c) for c in d.columns]
    ws.append(cols)
    for c in ws[2]:
        c.font = Font(name=_CN_FONT, bold=True, color="1F2937")
        c.fill = _HEAD
        c.border = _BORDER
        c.alignment = Alignment(vertical="center")

    if d.empty:
        ws.append(["（无数据）"])
    else:
        for row in d.itertuples(index=False, name=None):
            ws.append([_clean(v) for v in row])
        for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=False)
        ws.auto_filter.ref = "A2:%s%d" % (get_column_letter(len(cols)), ws.max_row)

    ws.freeze_panes = "A3"
    _fit(ws)


# 把一份 Report 渲染成 xlsx 的字节，Web UI 的下载按钮直接吃这个
def excel_bytes(report) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "报告说明"

    wide = report.meta.get("wide")
    has_wide = isinstance(wide, pd.DataFrame) and not wide.empty
    wide_name = "原始指标（可计算）"
    used = {"报告说明"}
    if has_wide:
        used.add(wide_name)

    tables = [s for s in report.sections if s["type"] == "table"]
    names = [_sheet_name(s.get("caption", ""), used) for s in tables]
    _write_text_sheet(ws, report, names,
                      "→ 原始指标点估计（纯数值，可直接计算）：见工作表「%s」" % wide_name
                      if has_wide else "")
    for s, name in zip(tables, names):
        d = s["df"]
        _write_data_sheet(wb.create_sheet(name),
                          d if d is not None else pd.DataFrame(), s.get("caption", ""))
    if has_wide:
        _write_data_sheet(wb.create_sheet(wide_name), wide,
                          "表 · 原始指标点估计（纯数值，可直接计算）")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# 把一份 Report 写成 xlsx 文件，返回落盘路径
def save_excel(report, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(excel_bytes(report))
    return path
