"""
PlayPulse 现场填写表 · Excel 生成脚本

用法：
    python scripts/make_playtest_sheet.py

产出：
    templates/试玩采集表.xlsx

结构（一个文件 = 一位玩家 × 一次试玩 × 一个版本）：
    ①现场填写表   —— 打开就落在这张表上，直接填
    ②填写说明     —— 口径与易错点
    ③编码字典     —— 下拉选项对照

布局：
    第 2-3 行：玩家信息（填一次，不用每行重复）
    第 5 行起：一行 = 一个关卡，关卡号已预填（N_LEVELS 控制，当前 3 关）
    D 列（卡关占比）、J 列（时间膨胀系数）自动计算，别手改

改字段请改本脚本的 COLS / META 后重跑，不要在 Excel 里手工插列（会丢公式和下拉）。
关卡数变了改 N_LEVELS 重跑。
"""

import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "templates", "试玩采集表.xlsx")

N_LEVELS = 3       # 预填关卡行数（当前游戏只有 3 关；加关卡改这里重跑）
META_ROW_LABEL = 2  # 玩家信息标签行
META_ROW_INPUT = 3  # 玩家信息填写行
HEAD_ROW = 5        # 列表头行
FIRST_ROW = 6       # 第一条数据
LAST_ROW = FIRST_ROW + N_LEVELS - 1

FONT = "微软雅黑"

C_INPUT = "FFF9E6"   # 顶部玩家信息输入格 - 浅黄
C_LEVEL = "FDE9D9"   # 关卡维度 - 橙
C_AUTO = "E2EFDA"    # 自动计算 - 绿
C_RED = "FFC7CE"

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

TITLE_FONT = Font(name=FONT, size=14, bold=True, color="1F3864")
SUB_FONT = Font(name=FONT, size=9, italic=True, color="808080")
H2_FONT = Font(name=FONT, size=11, bold=True, color="2E5C8A")
HEAD_FONT = Font(name=FONT, size=10, bold=True, color="1F3864")
BODY_FONT = Font(name=FONT, size=10)
META_LABEL_FONT = Font(name=FONT, size=9, bold=True, color="7F6000")
NOTE_FONT = Font(name=FONT, size=9, color="7F6000")
EX_FONT = Font(name=FONT, size=9, italic=True, color="808080")

# 玩家信息（顶部填一次）: (标签, 列宽, 下拉, 提示)
META = [
    ("玩家编号", 9,  None,              "P01、P02…同一人多次测试用同一个编号"),
    ("玩家水平", 11, ["老手", "新手"],   "老手=策划本人/同事/玩过同品类；新手=完全没玩过"),
    ("游戏版本", 12, None,              "v032。**每次调参数必须换号**，否则做不了版本对比"),
    ("日期",     11, None,              "2026-09-22"),
]

# 关卡维度列: (表头, 列宽, 数字格式, 下拉, 提示)
COLS = [
    ("关卡",           9,  "0",    None,              "关卡号，已预填"),
    ("耗时(秒)",       11, "0",    None,              "这关从进入到通关的总秒数"),
    ("卡关时长(秒)",   13, "0",    None,              "只算干等/卡住/反复失败的时间，正常游玩不算"),
    ("卡关占比",       11, "0.00", None,              "自动 = 卡关时长 ÷ 耗时。>0.4 标红"),
    ("尝试次数",       11, "0",    None,              "失败重来了几次（1 = 一次过）"),
    ("难度vs上一关",   13, "0",    [-2, -1, 0, 1, 2], "-2容易很多 -1容易一点 0差不多 +1难一点 +2难很多"),
    ("挫败感(1-5)",    12, "0",    [1, 2, 3, 4, 5],   "1完全不挫败 → 5非常挫败"),
    ("挑战感(1-5)",    12, "0",    [1, 2, 3, 4, 5],   "1无聊 → 5很有挑战。挫败低 + 挑战高 = 好关卡"),
    ("预估耗时(秒)",   13, "0",    None,              "**先问再看真实计时**，提前看到数字这题就废了"),
    ("时间膨胀系数",   13, "0.00", None,              "自动 = 预估 ÷ 实际。>1.3 标红（体感比实际漫长）"),
    ("卡关原因",       12, "0",    [0, 1, 2, 3, 4, 5, 9],
     "0没卡 1资源不够 2概率不好 3没看懂规则 4操作繁琐 5不知道干嘛 9其他"),
    ("卡关选择",       12, "0",    [0, 1, 2, 3, 4],
     "0没卡 1继续等 2看广告 3想付费 4想退出（**选 4 是最强流失信号**）"),
    ("备注",           28, "@",    None,              "卡关原因选 9 时在这里写原文；其他随手写"),
]

# 自动计算列: 列索引(1-based) -> (分子列字母, 分母列字母)
AUTO = {4: ("C", "B"), 10: ("I", "B")}


def add_dv(ws, values, col_letter, first, last, tip=""):
    dv = DataValidation(
        type="list",
        formula1='"%s"' % ",".join(str(v) for v in values),
        allow_blank=True,
        showDropDown=False,   # False = 显示下拉箭头（openpyxl 这个参数是反的）
    )
    if tip:
        dv.prompt = tip.replace("**", "")
        dv.promptTitle = "填写提示"
        dv.showInputMessage = True
    dv.error = "请从下拉列表中选择"
    dv.errorTitle = "取值不在编码表内"
    dv.showErrorMessage = True
    ws.add_data_validation(dv)
    dv.add("%s%d:%s%d" % (col_letter, first, col_letter, last))


def build_sheet(wb):
    ws = wb.create_sheet("①现场填写表")
    ws.sheet_view.showGridLines = False

    # 标题
    ws["A1"] = "PlayPulse · 现场填写表"
    ws["A1"].font = TITLE_FONT
    ws["B1"] = "一次试玩一份文件；下面一行 = 一个关卡，关卡号已预填好，直接填后面几列"
    ws["B1"].font = SUB_FONT
    ws.row_dimensions[1].height = 22

    # 玩家信息（填一次）
    for i, (label, width, dv, tip) in enumerate(META, start=1):
        L = get_column_letter(i)
        ws.column_dimensions[L].width = width

        c = ws.cell(row=META_ROW_LABEL, column=i, value=label)
        c.font = META_LABEL_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")

        v = ws.cell(row=META_ROW_INPUT, column=i)
        v.fill = PatternFill("solid", fgColor=C_INPUT)
        v.border = BORDER
        v.font = BODY_FONT
        v.alignment = Alignment(horizontal="center")
        v.number_format = "@"
        if dv:
            add_dv(ws, dv, L, META_ROW_INPUT, META_ROW_INPUT, tip)
    ws.row_dimensions[META_ROW_INPUT].height = 22

    # 列宽（关卡维度）
    for i, (name, width, fmt, dv, tip) in enumerate(COLS, start=1):
        cur = ws.column_dimensions[get_column_letter(i)].width or 0
        ws.column_dimensions[get_column_letter(i)].width = max(cur, width)

    # 列表头
    ws.row_dimensions[HEAD_ROW].height = 30
    for i, (name, width, fmt, dv, tip) in enumerate(COLS, start=1):
        c = ws.cell(row=HEAD_ROW, column=i, value=name)
        c.font = HEAD_FONT
        c.fill = PatternFill("solid", fgColor=C_LEVEL if i not in AUTO else C_AUTO)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER

    # 数据行
    for r in range(FIRST_ROW, LAST_ROW + 1):
        ws.cell(row=r, column=1, value=r - FIRST_ROW + 1).font = BODY_FONT
        for i, (name, width, fmt, dv, tip) in enumerate(COLS, start=1):
            cell = ws.cell(row=r, column=i)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.number_format = fmt
            cell.alignment = Alignment(horizontal="center")
            if i in AUTO:
                cell.fill = PatternFill("solid", fgColor=C_AUTO)
            elif i == len(COLS):
                cell.alignment = Alignment(horizontal="left")
        for i, (num, den) in AUTO.items():
            ws.cell(row=r, column=i,
                    value='=IF(OR({1}{0}="",{2}{0}="",{2}{0}=0),"",ROUND({1}{0}/{2}{0},2))'.format(r, num, den))

    for i, (name, width, fmt, dv, tip) in enumerate(COLS, start=1):
        if dv:
            add_dv(ws, dv, get_column_letter(i), FIRST_ROW, LAST_ROW, tip)

    # 条件格式
    for col_idx, thr in ((4, "0.4"), (10, "1.3")):
        L = get_column_letter(col_idx)
        ws.conditional_formatting.add(
            "%s%d:%s%d" % (L, FIRST_ROW, L, LAST_ROW),
            CellIsRule(operator="greaterThan", formula=[thr],
                       fill=PatternFill("solid", fgColor=C_RED),
                       font=Font(name=FONT, size=10, color="9C0006", bold=True)),
        )

    ws.freeze_panes = "B%d" % FIRST_ROW

    # 底部提示
    nr = LAST_ROW + 2
    ws.cell(row=nr, column=1,
            value="绿色两列自动算，别手改：卡关占比 > 0.4、时间膨胀系数 > 1.3 会自动标红。").font = NOTE_FONT
    ws.cell(row=nr + 1, column=1,
            value="最少只填 4 列也能用：耗时 / 卡关时长 / 难度vs上一关 / 挫败感。其余是下拉，点一下即可。").font = NOTE_FONT
    return ws


def build_intro(wb):
    ws = wb.create_sheet("②填写说明")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 92

    ws["B2"] = "怎么填"
    ws["B2"].font = TITLE_FONT

    r = 4

    def block(title, items):
        nonlocal r
        ws.cell(row=r, column=2, value=title).font = H2_FONT
        r += 1
        for k, v in items:
            ws.cell(row=r, column=2, value=k).font = Font(name=FONT, size=10, bold=True)
            c = ws.cell(row=r, column=3, value=v.replace("**", ""))
            c.font = BODY_FONT
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 30
            r += 1
        r += 1

    block("三步", [
        ("1 开玩前", "填顶部第 3 行四个格子：玩家编号 / 玩家水平 / 游戏版本 / 日期。填一次就够，不用每行重复。"),
        ("2 边玩边填", "第 6 行起，一行一关。最少填：耗时 / 卡关时长 / 难度vs上一关 / 挫败感。"),
        ("3 玩完存盘", "命名 试玩_P01_v032_2026-09-22.xlsx，丢进 PlayPulse/data/raw/。"),
    ])

    block("一份文件 = 一位玩家 × 一次试玩 × 一个版本", [
        ("多人怎么办", "复制这份文件，一人一份。别在同一份里混两个人的数据。"),
        ("版本号", "每次调参数必须换（v031 → v032）。这是做版本对比的唯一依据，不改就白测。"),
    ])

    block("玩家水平：老手 / 新手", [
        ("老手", "策划本人、组内同事、玩过同品类的人。知道解法、不卡关，跑出来的是最优路径时间 = 下界。"),
        ("新手", "完全没玩过这个品类的人。其时长中位数通常是老手的 2~5 倍。"),
        ("玩家水平为什么要分", "不分就没法把老手数据换算成玩家数据。"),
        ("最低成本做法", "同一版本、同一批关卡，找 1 个新手跑一遍。半天，就能测出真实倍差。"),
    ])

    block("三个容易填错的地方", [
        ("难度用相对分", "不是「这关有多难」，是「比上一关难多少」。绝对分每人锚点不同，跨人不可比。"),
        ("预估耗时必须盲测", "先问「你觉得这关花了多久」，再看真实计时。提前看到数字这题就废了。"),
        ("玩家水平别全填老手", "老手知道解法、不卡关，跑出来的是下界。全填老手，这份数据就只能当下界用。"),
    ])

    block("自动列口径", [
        ("卡关占比", "= 卡关时长 ÷ 耗时。> 0.4 标红 → 这关大部分时间在干等。"),
        ("时间膨胀系数", "= 预估耗时 ÷ 实际耗时。> 1.3 标红 → 体感比实际漫长，说明枯燥或干等。"),
    ])
    return ws


def build_dict(wb):
    ws = wb.create_sheet("③编码字典")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 60

    ws["B2"] = "编码对照"
    ws["B2"].font = TITLE_FONT
    ws["B3"] = "表里的下拉选项都在这里，不用背"
    ws["B3"].font = SUB_FONT

    groups = [
        ("玩家水平", [("老手", "策划本人 / 组内同事 / 玩过同品类"),
                      ("新手", "完全没玩过这个品类")]),
        ("难度vs上一关", [("-2", "容易很多"), ("-1", "容易一点"), ("0", "差不多"),
                          ("+1", "难一点"), ("+2", "难很多")]),
        ("挫败感", [("1", "完全不挫败"), ("2", "有点烦"), ("3", "一般"),
                    ("4", "比较挫败"), ("5", "非常挫败")]),
        ("挑战感", [("1", "无聊"), ("2", "偏低"), ("3", "一般"),
                    ("4", "有挑战"), ("5", "很有挑战")]),
        ("卡关原因", [("0", "没卡"), ("1", "资源不够"), ("2", "概率不好"),
                      ("3", "没看懂规则"), ("4", "操作繁琐"),
                      ("5", "不知道该干嘛"), ("9", "其他（在备注写原文）")]),
        ("卡关选择", [("0", "没卡"), ("1", "继续等"), ("2", "看广告"),
                      ("3", "想付费"), ("4", "想退出 ← 最强流失信号")]),
    ]

    r = 5
    for title, items in groups:
        ws.cell(row=r, column=2, value=title).font = H2_FONT
        r += 1
        for k, v in items:
            ws.cell(row=r, column=2, value=k).font = BODY_FONT
            ws.cell(row=r, column=3, value=v).font = BODY_FONT
            r += 1
        r += 1
    return ws


def main():
    wb = Workbook()
    wb.remove(wb.active)
    build_sheet(wb)   # 第一个创建 = 打开时默认落在这一页
    build_intro(wb)
    build_dict(wb)
    wb.active = 0
    wb.properties.title = "PlayPulse 现场填写表"
    wb.properties.creator = "PlayPulse"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wb.save(OUT)
    print("OK ->", OUT)


if __name__ == "__main__":
    main()
