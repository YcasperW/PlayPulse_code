"""
PlayPulse 试玩记录表（精简单表版）· Excel 生成脚本

用法：
    python scripts/make_playtest_sheet.py

产出：
    templates/试玩采集表.xlsx

设计：
    一张表搞定。一行 = 一个玩家 × 一个关卡。
    左侧 4 列是玩家维度（填一次往下拖），右侧是关卡维度（每关填）。
    E/H/N 三列自动计算，别手改。

如需加回字段（主动时长/开箱数/资源缺口/求助/广告/录屏行为），
改本脚本的 COLS 后重跑即可，不要手工在 Excel 里插入列。
"""

import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "templates", "试玩采集表.xlsx")

ROWS = 300          # 可填行数（玩家数 × 关卡数）
FONT = "微软雅黑"

C_PLAYER = "D9E7F5"   # 玩家维度 - 蓝
C_LEVEL = "FDE9D9"    # 关卡维度 - 橙
C_AUTO = "E2EFDA"     # 自动计算 - 绿
C_RED = "FFC7CE"

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEAD_FONT = Font(name=FONT, size=10, bold=True, color="1F3864")
BODY_FONT = Font(name=FONT, size=10)
TITLE_FONT = Font(name=FONT, size=14, bold=True, color="1F3864")
H2_FONT = Font(name=FONT, size=11, bold=True, color="2E5C8A")
NOTE_FONT = Font(name=FONT, size=9, color="7F6000")

# 列定义: (表头, 列宽, 分区色, 数字格式, 下拉选项, 悬浮提示)
COLS = [
    ("玩家编号",      10, C_PLAYER, "@",    None, "P01、P02…同一人多次测试用同号"),
    ("玩家水平",      11, C_PLAYER, "@",    ["老手", "新手"],
     "老手=策划本人/组内同事/玩过同品类；新手=完全没玩过。**新手时长中位数通常是老手的 2~5 倍**"),
    ("游戏版本",      12, C_PLAYER, "@",    None, "如 v032。**每次调参必须改**，否则做不了版本对比"),
    ("日期",          12, C_PLAYER, "@",    None, "2026-09-22"),

    ("关卡",          7,  C_LEVEL, "0",     None, "关卡号"),
    ("耗时(秒)",      11, C_LEVEL, "0",     None, "这一关从进入到通关的总秒数"),
    ("卡关时长(秒)",  13, C_LEVEL, "0",     None, "只算干等/卡住/反复失败的时间，正常游玩不算"),
    ("卡关占比",      11, C_AUTO,  "0.00",  None, "自动 = 卡关时长 ÷ 耗时。>0.4 标红"),
    ("尝试次数",      11, C_LEVEL, "0",     None, "这关失败重来了几次（1=一次过）"),
    ("难度vs上一关\n(-2~+2)", 13, C_LEVEL, "0", [-2, -1, 0, 1, 2],
     "-2容易很多 -1容易一点 0差不多 +1难一点 +2难很多。**不是绝对分**"),
    ("挫败感(1-5)",   12, C_LEVEL, "0",     [1, 2, 3, 4, 5], "1完全不挫败 → 5非常挫败"),
    ("挑战感(1-5)",   12, C_LEVEL, "0",     [1, 2, 3, 4, 5], "1无聊 → 5很有挑战。挫败低+挑战高=好关卡"),
    ("预估耗时(秒)",  13, C_LEVEL, "0",     None, "**先问再看真实计时**，提前看到这题就废了"),
    ("时间膨胀系数",  13, C_AUTO,  "0.00",  None, "自动 = 预估 ÷ 实际。>1.3 标红（体感比实际漫长）"),
    ("卡关原因\n(0-5,9)", 12, C_LEVEL, "0", [0, 1, 2, 3, 4, 5, 9],
     "0没卡 1资源不够 2概率不好 3没看懂规则 4操作繁琐 5不知道干嘛 9其他"),
    ("卡关选择\n(0-4)",   11, C_LEVEL, "0", [0, 1, 2, 3, 4],
     "0没卡 1继续等 2看广告 3想付费 4想退出（**最强流失信号**）"),
]

AUTO_COLS = {"H": ("G", "F"), "N": ("M", "F")}   # 目标列: (分子, 分母)


def add_dv(ws, values, col_letter, first, last, tip=""):
    dv = DataValidation(
        type="list",
        formula1='"%s"' % ",".join(str(v) for v in values),
        allow_blank=True,
        showDropDown=False,   # False = 显示下拉箭头（openpyxl 命名是反的）
    )
    if tip:
        dv.prompt = tip
        dv.promptTitle = "填写提示"
        dv.showInputMessage = True
    dv.error = "请从下拉列表中选择"
    dv.errorTitle = "取值不在编码表内"
    dv.showErrorMessage = True
    ws.add_data_validation(dv)
    dv.add("%s%d:%s%d" % (col_letter, first, col_letter, last))


def build_intro(wb):
    ws = wb.create_sheet("①填写说明")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 92

    ws["B2"] = "PlayPulse · 试玩记录表"
    ws["B2"].font = TITLE_FONT
    ws["B3"] = "就一张表。一行 = 一个玩家 × 一个关卡。"
    ws["B3"].font = Font(name=FONT, size=9, italic=True, color="808080")

    r = 5

    def block(title, items):
        nonlocal r
        ws.cell(row=r, column=2, value=title).font = H2_FONT
        r += 1
        for k, v in items:
            ws.cell(row=r, column=2, value=k).font = Font(name=FONT, size=10, bold=True)
            c = ws.cell(row=r, column=3, value=v)
            c.font = BODY_FONT
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 30
            r += 1
        r += 1

    block("怎么填（三步）", [
        ("1", "前 4 列（玩家编号/水平/版本/日期）只填第一行，选中往下拖就行。"),
        ("2", "然后每关一行，填 关卡 / 耗时 / 卡关时长 / 难度vs上一关。这四列是骨架。"),
        ("3", "卡住了才填最后两列归因；没卡住填 0。"),
    ])

    block("玩家水平：老手 / 新手", [
        ("老手", "策划本人、组内同事、玩过同品类的人。知道解法、不卡关，跑出来的是**最优路径时间 = 下界**。"),
        ("新手", "完全没玩过这个品类的人。其时长中位数通常是老手的 2~5 倍。"),
        ("为什么要分", "不分就没法把老手数据换算成玩家数据。"),
        ("最低成本做法", "同一版本、同一批关卡，找 1 个新手跑一遍。半天，就能测出真实的倍差。"),
    ])

    block("绿色列自动算，别手改", [
        ("卡关占比", "= 卡关时长 ÷ 耗时。**>0.4 自动标红** → 这关大部分时间在干等。"),
        ("时间膨胀系数", "= 预估耗时 ÷ 实际耗时。**>1.3 自动标红** → 体感比实际漫长，说明枯燥或干等。"),
    ])

    block("三个容易填错的地方", [
        ("难度用相对分", "不是「这关有多难」，是「比上一关难多少」。绝对分每人锚点不同，跨人不可比。"),
        ("预估耗时必须盲测", "问完「你觉得这关花了多久」再看真实计时。提前看到数字就没意义了。"),
        ("玩家水平别全填老手", "老手（含策划）知道解法、不卡关，跑出来的是最优路径时间 = **下界**。新手中位数通常是它的 2~5 倍。全填老手，这份数据就只能当下界用。"),
    ])

    block("编码对照（最后两列）", [
        ("卡关原因", "0没卡 1资源不够 2概率不好 3没看懂规则 4操作繁琐 5不知道干嘛 9其他（备注留原文）"),
        ("卡关选择", "0没卡 1继续等 2看广告 3想付费 4想退出 ← **选 4 是最强流失信号**"),
    ])

    block("存文件", [
        ("命名", "试玩_P01_策划_v032_2026-09-22.xlsx（一人一份；多人也可放一个文件，靠玩家编号区分）"),
        ("位置", "填完丢进 PlayPulse/data/raw/，我批量读。"),
        ("版本号", "每次调参数必须换（v031→v032），这是做版本对比的唯一依据。"),
    ])

    block("示例行（第 2 行，正式填之前删掉）", [
        ("第 7 关", "耗时 180、卡关 95、尝试 4、难度 +2、挫败 4、预估 300 → 卡关占比 0.53(红)、膨胀 1.67(红)。典型断崖关。"),
        ("第 8 关", "耗时 95、卡关 10、难度 -1、挫败 2、预估 90 → 两项都正常。7→8 的落差本身就是问题。"),
    ])
    return ws


def build_sheet(wb):
    ws = wb.create_sheet("②试玩记录")

    for i, (name, width, color, fmt, dv, tip) in enumerate(COLS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
        c = ws.cell(row=1, column=i, value=name)
        c.font = HEAD_FONT
        c.fill = PatternFill("solid", fgColor=color)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[1].height = 34

    for r in range(2, ROWS + 2):
        for i, (name, width, color, fmt, dv, tip) in enumerate(COLS, start=1):
            cell = ws.cell(row=r, column=i)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.number_format = fmt
            cell.alignment = Alignment(horizontal="center")
            if i <= 4:
                cell.fill = PatternFill("solid", fgColor="F7FBFF")
            elif i in (8, 14):
                cell.fill = PatternFill("solid", fgColor=C_AUTO)

        # 自动计算列
        for col, (num, den) in AUTO_COLS.items():
            ws.cell(row=r, column=COLS_COLINDEX[col],
                    value='=IF(OR({1}{0}="",{2}{0}=""),"",ROUND({1}{0}/{2}{0},2))'.format(
                        r, num, den))

    for i, (name, width, color, fmt, dv, tip) in enumerate(COLS, start=1):
        if dv:
            L = get_column_letter(i)
            add_dv(ws, dv, L, 2, ROWS + 1, tip)

    # 条件格式
    ws.conditional_formatting.add(
        "H2:H%d" % (ROWS + 1),
        CellIsRule(operator="greaterThan", formula=["0.4"],
                   fill=PatternFill("solid", fgColor=C_RED),
                   font=Font(name=FONT, size=10, color="9C0006", bold=True)),
    )
    ws.conditional_formatting.add(
        "N2:N%d" % (ROWS + 1),
        CellIsRule(operator="greaterThan", formula=["1.3"],
                   fill=PatternFill("solid", fgColor=C_RED),
                   font=Font(name=FONT, size=10, color="9C0006", bold=True)),
    )

    ws.freeze_panes = "E2"
    ws.auto_filter.ref = "A1:P%d" % (ROWS + 1)

    # 示例行
    ex = ["P01", "老手", "v032", "2026-09-22", 7, 180, 95, None, 4, 2, 4, 3, 300, None, 1, 1]
    for i, v in enumerate(ex, start=1):
        if v is None:
            continue
        c = ws.cell(row=2, column=i, value=v)
        c.font = Font(name=FONT, size=9, italic=True, color="808080")

    nr = ROWS + 3
    ws.cell(row=nr, column=1,
            value="说明：绿色列自动算；卡关占比>0.4、时间膨胀系数>1.3 自动标红。第 2 行是示例，正式填之前删掉。").font = NOTE_FONT
    ws.cell(row=nr + 1, column=1,
            value="口径：耗时 = 主动游玩 + 卡关时长；难度是相对上一关的增量，不是绝对分；预估耗时必须盲测。").font = NOTE_FONT
    return ws


COLS_COLINDEX = {chr(ord("A") + i): i + 1 for i in range(len(COLS))}


def main():
    wb = Workbook()
    wb.remove(wb.active)
    build_intro(wb)
    build_sheet(wb)
    wb.properties.title = "PlayPulse 试玩记录表"
    wb.properties.creator = "PlayPulse"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wb.save(OUT)
    print("OK ->", OUT)


if __name__ == "__main__":
    main()
