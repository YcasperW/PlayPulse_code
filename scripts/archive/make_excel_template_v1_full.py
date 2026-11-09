"""
PlayPulse 试玩采集表 · Excel 生成脚本

用法：
    python scripts/make_excel_template.py

产出：
    templates/试玩采集表.xlsx

设计来源：
    docs/02-数据采集方案-内部试玩.md  §6 采集表
    docs/03-指标与埋点字典.md
    templates/编码字典-文本转数字.md
    templates/逐关-现场填写表.csv  /  逐关-录屏回看编码表.csv

改字段后重跑本脚本即可，不要手工改 Excel（会丢公式与下拉）。
"""

import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "templates", "试玩采集表.xlsx")

ROWS = 40          # 预留关卡行数
FONT = "微软雅黑"

# 配色（浅色主题，打印友好）
C_HEAD_OBJ = "D9E7F5"   # 客观区 - 蓝
C_HEAD_SUB = "FDE9D9"   # 主观区 - 橙
C_HEAD_CAU = "E4E7F2"   # 归因区 - 紫灰
C_HEAD_AUTO = "E2EFDA"  # 自动计算 - 绿
C_NOTE = "FFF7E6"       # 说明底
C_EX = "F2F2F2"         # 示例行底

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEAD_FONT = Font(name=FONT, size=10, bold=True, color="1F3864")
BODY_FONT = Font(name=FONT, size=10)
NOTE_FONT = Font(name=FONT, size=9, color="7F6000")
EX_FONT = Font(name=FONT, size=9, italic=True, color="808080")
TITLE_FONT = Font(name=FONT, size=14, bold=True, color="1F3864")
H2_FONT = Font(name=FONT, size=11, bold=True, color="2E5C8A")


def style_header(ws, row, col, fill):
    c = ws.cell(row=row, column=col)
    c.font = HEAD_FONT
    c.fill = PatternFill("solid", fgColor=fill)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = BORDER
    return c


def add_dv(ws, values, col_letter, first=2, last=ROWS + 1, tip="", title=""):
    """给某一列加下拉。values 为可选项列表。"""
    dv = DataValidation(
        type="list",
        formula1='"%s"' % ",".join(str(v) for v in values),
        allow_blank=True,
        showDropDown=False,   # False = 显示下拉箭头（openpyxl 的命名是反的）
    )
    if tip:
        dv.prompt = tip
        dv.promptTitle = title or "填写提示"
        dv.showInputMessage = True
    dv.error = "请从下拉列表中选择"
    dv.errorTitle = "取值不在编码表内"
    dv.showErrorMessage = True
    ws.add_data_validation(dv)
    dv.add("%s%d:%s%d" % (col_letter, first, col_letter, last))


# Sheet ① 填写说明
def build_intro(wb):
    ws = wb.create_sheet("①填写说明")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 96

    ws["B2"] = "PlayPulse · 试玩采集表"
    ws["B2"].font = TITLE_FONT
    ws["B3"] = "内部试玩，一人一份。打完就填，不要攒到最后回忆（回忆会被峰终效应污染）。"
    ws["B3"].font = Font(name=FONT, size=9, italic=True, color="808080")

    blocks = [
        ("一份文件 = 一个人 × 一次试玩 × 一个版本", [
            ("②本次试玩信息", "开头先填一次（12 项）。谁玩的、哪个版本、哪天。没有这个，版本对比做不了。"),
            ("③逐关-现场填写表", "边玩边填。每关一行，主表。最少只需填：关卡 / 耗时 / 卡关时长 / 难度vs上一关。"),
            ("④录屏回看编码表", "录屏后回看补填。没录屏可以整张空着，不影响主流程。"),
            ("⑤整体问卷", "玩完之后 5 分钟内填一次。10 问里 9 问产出数字。"),
            ("⑥编码字典", "编码对照表，不用填。卡关原因/卡关选择看这里。"),
        ]),
        ("填写顺序", [
            ("0", "开玩前先填 ②本次试玩信息（编号、是否策划、版本号、日期）。"),
            ("1", "边玩边填 ③的 A列关卡号 与 B列耗时（秒），这两列是全部分析的骨架。"),
            ("2", "每关打完立刻填 ③的 I列「难度vs上一关」，隔了再问就不准了。"),
            ("3", "卡住了才填 N/O 两列归因；没卡住填 0。"),
            ("4", "玩完立刻填 ⑤整体问卷。再之后有录屏才回看补 ④。"),
        ]),
        ("三个容易错的地方", [
            ("难度用相对分", "不是「这关有多难」，是「比上一关难多少」。-2容易很多 / 0差不多 / +2难很多。绝对分每人锚点不同，跨人不可比。"),
            ("卡关时长 ≠ 耗时", "卡关时长只算「干等、卡住、反复失败」的时间。正常游玩时间算进主动时长。两者相加≈耗时。"),
            ("预估耗时必须盲测", "问完「你觉得这关花了多久」再看真实计时。提前看到数字就没意义了。"),
        ]),
        ("自动计算的列（别手改）", [
            ("③表 E 卡关占比", "= 卡关时长 / 耗时。> 0.4 标红，说明这关大部分时间在干等。"),
            ("③表 M 时间膨胀系数", "= 预估耗时 / 实际耗时。> 1.3 标红，说明体感比实际漫长（枯燥/干等）。"),
            ("④表 E 无效操作率", "= 无效操作数 / 总操作数。> 0.2 标红。"),
            ("④表 H APM", "= 总操作数 / (总时长/60)。总时长填在 ④表 B43。"),
            ("⑤表 整体膨胀系数", "= 问卷预估时长 / 暗中计时实际时长。引用 ②表 B8。"),
        ]),
        ("文件怎么存", [
            ("一人一份", "复制本文件，重命名后填。命名：试玩_P01_是否策划1_v032_2026-09-22.xlsx"),
            ("版本号必须变", "每次调参数就换版本号（v031→v032），这是做版本对比 Diff 的唯一依据。"),
            ("放到 data/raw/", "填完统一丢进 PlayPulse/data/raw/，我批量读。"),
        ]),
    ]

    r = 5
    for title, items in blocks:
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

    ws.cell(row=r, column=2, value="示例（填之前不用删，这只是说明页）").font = H2_FONT
    r += 1
    for k, v in [
        ("第 7 关", "耗时 180 秒，其中卡关 95 秒，尝试 4 次，难度 +2，挫败 4，预估 300 秒 → 卡关占比 0.53（红），膨胀系数 1.67（红）。这是典型的断崖关。"),
        ("第 8 关", "耗时 95 秒，卡关 10 秒，难度 -1，挫败 2，预估 90 秒 → 两项都正常。第 7→8 的落差本身就是问题。"),
    ]:
        ws.cell(row=r, column=2, value=k).font = Font(name=FONT, size=10, bold=True)
        c = ws.cell(row=r, column=3, value=v)
        c.font = BODY_FONT
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 34
        r += 1
    return ws


# Sheet ② 本次试玩信息（元信息，一人一次）
META_ROWS = [
    ("受访者编号", None, "建议 P01、P02…同一人多次测试用同号"),
    ("是否策划(1/0)", [1, 0], "1=是 0=否。**策划数据是下界**，必须区分，否则会把最优路径时间当玩家时间"),
    ("玩过同品类(1/0)", [1, 0], "1=老手 0=真新手。影响倍差校准"),
    ("游戏版本/构建号", None, "如 v0.3.2。**每次调参必须改**，否则无法做版本对比 Diff"),
    ("测试批次", None, "如 batch-2026-09-22，同一轮测试用同号"),
    ("日期", None, "yyyy-mm-dd"),
    ("实际游玩时长(分钟)", None, "暗中计时，**不要告知本人**。问卷 Q1 要拿它算整体膨胀系数"),
    ("到达关卡", None, "他玩到第几关"),
    ("自然停止关卡", None, "不问「还想玩吗」，看他自己什么时候放下"),
    ("次日自发回归(0/1)", [1, 0], "第二天**不提醒**，看他会不会自己打开。小样本下最接近次日留存的信号"),
    ("设备/平台", None, "选填，如 PC / iOS / Android"),
    ("备注", None, "选填。异常记录在这里，不参与统计"),
]

META_ACTUAL_MIN_ROW = 4 + [n for n, _, _ in META_ROWS].index("实际游玩时长(分钟)")  # =10


def build_meta(wb):
    ws = wb.create_sheet("②本次试玩信息")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABC", (22, 20, 74)):
        ws.column_dimensions[col].width = w

    ws["A1"] = "本次试玩信息 · 一人一次，开玩前先填"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "没有这张表，逐关数据无法归因到「谁 / 哪个版本」，版本对比 Diff 就跑不了。"
    ws["A2"].font = Font(name=FONT, size=9, italic=True, color="9C0006")

    for i, h in enumerate(["字段", "值", "说明 / 选项"], start=1):
        style_header(ws, 3, i, C_HEAD_OBJ)
        ws.cell(row=3, column=i, value=h)

    r = 4
    for name, dv_values, desc in META_ROWS:
        ws.cell(row=r, column=1, value=name).font = Font(name=FONT, size=10, bold=True)
        ws.cell(row=r, column=1).border = BORDER
        ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor="F7F9FC")

        vc = ws.cell(row=r, column=2)
        vc.border = BORDER
        vc.font = Font(name=FONT, size=11, bold=True)
        vc.fill = PatternFill("solid", fgColor="FFF2CC")
        vc.alignment = Alignment(horizontal="center")

        c = ws.cell(row=r, column=3, value=desc)
        c.font = NOTE_FONT
        c.border = BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = 22

        if dv_values:
            add_dv(ws, dv_values, "B", first=r, last=r)
        r += 1

    assert META_ACTUAL_MIN_ROW == 4 + [n for n, _, _ in META_ROWS].index("实际游玩时长(分钟)"), \
        "META_ACTUAL_MIN_ROW 与实际行号不一致"
    return ws


# Sheet ③ 逐关-现场填写表
def build_main(wb):
    ws = wb.create_sheet("③逐关-现场填写表")

    # (表头, 列宽, 分区色, 是否自动计算, 数字格式)
    cols = [
        ("关卡",            6,  C_HEAD_OBJ,  False, "0"),
        ("耗时(秒)",        10, C_HEAD_OBJ,  False, "0"),
        ("主动时长(秒)",    12, C_HEAD_OBJ,  False, "0"),
        ("卡关时长(秒)",    12, C_HEAD_OBJ,  False, "0"),
        ("卡关占比",        10, C_HEAD_AUTO, True,  "0.00"),
        ("尝试次数",        10, C_HEAD_OBJ,  False, "0"),
        ("开箱数",          9,  C_HEAD_OBJ,  False, "0"),
        ("资源缺口",        10, C_HEAD_OBJ,  False, "0"),
        ("难度vs上一关\n(-2~+2)", 13, C_HEAD_SUB, False, "0"),
        ("挫败感(1-5)",     11, C_HEAD_SUB,  False, "0"),
        ("挑战感(1-5)",     11, C_HEAD_SUB,  False, "0"),
        ("预估耗时(秒)",    12, C_HEAD_SUB,  False, "0"),
        ("时间膨胀系数",    12, C_HEAD_AUTO, True,  "0.00"),
        ("卡关原因编码\n(0-5,9)", 13, C_HEAD_CAU, False, "0"),
        ("卡关选择\n(0-4)", 10, C_HEAD_CAU,  False, "0"),
        ("求助次数",        10, C_HEAD_CAU,  False, "0"),
        ("用加速/广告\n(1/0)", 11, C_HEAD_CAU, False, "0"),
    ]

    for i, (name, width, color, auto, fmt) in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
        style_header(ws, 1, i, color)
        ws.cell(row=1, column=i, value=name)

    ws.row_dimensions[1].height = 34

    for r in range(2, ROWS + 2):
        ws.cell(row=r, column=1, value=r - 1).font = BODY_FONT
        # 卡关占比 = D / B
        ws.cell(row=r, column=5,
                value='=IF(OR(B{0}="",D{0}=""),"",ROUND(D{0}/B{0},2))'.format(r))
        # 时间膨胀系数 = 预估 L / 实际 B   （定义见 docs/01 §时间膨胀异常关卡）
        ws.cell(row=r, column=13,
                value='=IF(OR(B{0}="",L{0}=""),"",ROUND(L{0}/B{0},2))'.format(r))
        for c in range(1, len(cols) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = BODY_FONT
            cell.border = BORDER
            if cols[c - 1][4] and c not in (5, 13):
                cell.number_format = cols[c - 1][4]
            if c in (5, 13):
                cell.number_format = "0.00"
                cell.fill = PatternFill("solid", fgColor=C_HEAD_AUTO)
            cell.alignment = Alignment(horizontal="center")

    # 下拉
    add_dv(ws, [-2, -1, 0, 1, 2], "I",
           tip="-2容易很多 / -1容易一点 / 0差不多 / +1难一点 / +2难很多",
           title="相对上一关的难度")
    add_dv(ws, [1, 2, 3, 4, 5], "J", tip="1完全不挫败 → 5非常挫败", title="挫败感")
    add_dv(ws, [1, 2, 3, 4, 5], "K", tip="1无聊 → 5很有挑战（挑战感高+挫败感低=好关卡）", title="挑战感")
    add_dv(ws, [0, 1, 2, 3, 4, 5, 9], "N",
           tip="0没卡 1资源不够 2概率不好 3没看懂规则 4操作繁琐 5不知道干嘛 9其他",
           title="卡关原因")
    add_dv(ws, [0, 1, 2, 3, 4], "O",
           tip="0没卡 1继续等 2看广告 3想付费 4想退出（最强流失信号）",
           title="卡关时的选择")
    add_dv(ws, [1, 0], "Q", tip="1=用了加速或看了广告，0=没用", title="用加速/广告")

    # 条件格式：卡关占比 > 0.4 标红；膨胀系数 > 1.3 标红
    red = PatternFill("solid", fgColor="FFC7CE")
    for col in ("E", "M"):
        rng = "%s2:%s%d" % (col, col, ROWS + 1)
        ws.conditional_formatting.add(
            rng,
            CellIsRule(operator="greaterThan", formula=["0.4" if col == "E" else "1.3"],
                       fill=red, font=Font(name=FONT, size=10, color="9C0006", bold=True)),
        )

    ws.freeze_panes = "B2"
    ws.auto_filter.ref = "A1:Q%d" % (ROWS + 1)

    # 底部注释
    nr = ROWS + 3
    ws.cell(row=nr, column=1,
            value="说明：绿色列自动计算，不要手改。卡关占比>0.4、时间膨胀系数>1.3 会自动标红。").font = NOTE_FONT
    ws.cell(row=nr + 1, column=1,
            value="口径：耗时 ≈ 主动时长 + 卡关时长；难度为相对上一关的增量，不是绝对分。").font = NOTE_FONT
    return ws


# Sheet ③ 录屏回看编码表
def build_replay(wb):
    ws = wb.create_sheet("④录屏回看编码表")
    cols = [
        ("关卡", 6, C_HEAD_OBJ),
        ("总操作数", 10, C_HEAD_OBJ),
        ("有效操作数", 11, C_HEAD_OBJ),
        ("无效操作数", 11, C_HEAD_OBJ),
        ("无效操作率", 11, C_HEAD_AUTO),
        ("最长停顿(秒)", 12, C_HEAD_OBJ),
        ("停顿>5秒次数", 12, C_HEAD_OBJ),
        ("每分钟操作数\n(APM)", 12, C_HEAD_AUTO),
        ("跳过动画/引导\n次数", 13, C_HEAD_CAU),
        ("切屏/看手机\n次数", 12, C_HEAD_CAU),
        ("口头抱怨次数", 12, C_HEAD_CAU),
        ("重复无意义\n操作次数", 13, C_HEAD_CAU),
    ]
    for i, (name, width, color) in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
        style_header(ws, 1, i, color)
        ws.cell(row=1, column=i, value=name)
    ws.row_dimensions[1].height = 34

    total_row = ROWS + 3          # 43
    for r in range(2, ROWS + 2):
        ws.cell(row=r, column=1, value=r - 1).font = BODY_FONT
        ws.cell(row=r, column=5,
                value='=IF(OR(B{0}="",D{0}=""),"",ROUND(D{0}/B{0},2))'.format(r))
        # APM = 总操作数 / (本关总时长/60)；总时长填在 B{total_row}
        ws.cell(row=r, column=8,
                value='=IF(OR(B{0}="",$B${1}=""),"",ROUND(B{0}/($B${1}/60),1))'.format(r, total_row))
        for c in range(1, len(cols) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.number_format = "0.00" if c in (5, 8) else "0"
            if c in (5, 8):
                cell.fill = PatternFill("solid", fgColor=C_HEAD_AUTO)
            cell.alignment = Alignment(horizontal="center")

    # 总时长输入格（原 CSV 引用 $B$42 是空行，这里修正为实际输入行）
    ws.cell(row=total_row, column=1, value="本关总时长(秒) →").font = Font(
        name=FONT, size=10, bold=True, color="9C0006")
    tc = ws.cell(row=total_row, column=2)
    tc.fill = PatternFill("solid", fgColor="FFF2CC")
    tc.border = Border(left=Side(style="medium", color="9C0006"),
                       right=Side(style="medium", color="9C0006"),
                       top=Side(style="medium", color="9C0006"),
                       bottom=Side(style="medium", color="9C0006"))
    tc.font = Font(name=FONT, size=11, bold=True)
    tc.number_format = "0"
    ws.cell(row=total_row, column=3,
            value="← 填在这里，APM 列才有值").font = NOTE_FONT

    ws.conditional_formatting.add(
        "E2:E%d" % (ROWS + 1),
        CellIsRule(operator="greaterThan", formula=["0.2"],
                   fill=PatternFill("solid", fgColor="FFC7CE"),
                   font=Font(name=FONT, size=10, color="9C0006", bold=True)),
    )
    ws.freeze_panes = "B2"
    return ws


# Sheet ⑤ 整体问卷（玩完后填一次）
# (题号, 题目, 下拉选项, 自动计算, 说明)
SURVEY = [
    ("Q1", "你觉得自己玩了多久？（分钟）", None,
     '=IF(OR(C{0}="",\'②本次试玩信息\'!$B$%d=""),"",ROUND(C{0}/\'②本次试玩信息\'!$B$%d,2))'
     % (META_ACTUAL_MIN_ROW, META_ACTUAL_MIN_ROW),
     "**先问再看计时**。>1.3 说明体感比实际漫长（枯燥/干等）"),
    ("Q2a", "你是从第几关开始觉得「不想玩了」？", None, None,
     "数字关卡号。与难度曲线断崖点叠在一起看，重合处=真实流失点"),
    ("Q2b", "　└ 原因编码", [1, 2, 3, 4, 5], None,
     "1资源不够 2概率不好 3没看懂 4操作繁琐 5不知道要干嘛"),
    ("Q3-1", "最想删的第 1 关", None, None, "用排序不用打分——人对排序的判断准得多"),
    ("Q3-2", "最想删的第 2 关", None, None, ""),
    ("Q3-3", "最想删的第 3 关", None, None, ""),
    ("Q4", "卡关时如果只能选一个，你选？", [1, 2, 3, 4], None,
     "1继续等 2看广告 3花6元 4直接退出。**选4=最强流失信号**"),
    ("Q5", "你觉得下一个箱子出稀有物的概率是？（%）", None, None,
     "感知-实际：<-15pt 保底没被感知；>+15pt 预期被吊高"),
    ("Q6", "明天你会自己打开这个游戏吗？（1-5）", [1, 2, 3, 4, 5], None,
     "⚠️ 问完**真的不要安排第二天**，次日自发回归才是有效信号"),
    ("Q7", "你会推荐给朋友吗？（1-5）", [1, 2, 3, 4, 5], None, ""),
    ("Q8", "你觉得一个普通新手会在第几关流失？", None, None,
     "投射法。问「别人」时戒心降低，比直接问「你觉得难吗」更能拿实话"),
    ("Q9a", "开箱最爽的是第几关？", None, None, ""),
    ("Q9b", "最没感觉的是第几关？", None, None, ""),
    ("Q10", "（唯一开放题）有没有哪个瞬间你想退出，但碍于在测试就继续了？", None, None,
     "不参与统计，只用于解释上面数字里的异常点"),
]


def build_survey(wb):
    ws = wb.create_sheet("⑤整体问卷")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDE", (8, 46, 14, 16, 58)):
        ws.column_dimensions[col].width = w

    ws["A1"] = "整体问卷 · 玩完后 5 分钟内填写"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "设计原则：10 问里 9 问产出数字。开放题只有 Q10，且只用于解释异常。书面匿名填写，不要口头问。"
    ws["A2"].font = Font(name=FONT, size=9, italic=True, color="808080")

    for i, h in enumerate(["题号", "题目", "答案", "自动计算", "说明"], start=1):
        style_header(ws, 3, i, C_HEAD_SUB)
        ws.cell(row=3, column=i, value=h)

    r = 4
    for qid, text, dv_values, formula, note in SURVEY:
        ws.cell(row=r, column=1, value=qid).font = Font(name=FONT, size=10, bold=True)
        ws.cell(row=r, column=1).border = BORDER
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="center", vertical="center")

        c = ws.cell(row=r, column=2, value=text)
        c.font = BODY_FONT
        c.border = BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center")

        ac = ws.cell(row=r, column=3)
        ac.border = BORDER
        ac.font = Font(name=FONT, size=11, bold=True)
        ac.fill = PatternFill("solid", fgColor="FFF2CC")
        ac.alignment = Alignment(horizontal="center")

        calc = ws.cell(row=r, column=4, value=formula.format(r) if formula else None)
        calc.border = BORDER
        calc.font = Font(name=FONT, size=10, bold=True, color="1F6F3D")
        calc.fill = PatternFill("solid", fgColor=C_HEAD_AUTO)
        calc.alignment = Alignment(horizontal="center")

        n = ws.cell(row=r, column=5, value=note)
        n.font = NOTE_FONT
        n.border = BORDER
        n.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = 26

        if dv_values:
            add_dv(ws, dv_values, "C", first=r, last=r)
        r += 1

    # 爽感衰减跨度 = Q9b - Q9a
    i9a = 4 + [q[0] for q in SURVEY].index("Q9a")
    i9b = 4 + [q[0] for q in SURVEY].index("Q9b")
    ws.cell(row=r + 1, column=2, value="爽感衰减跨度（Q9b − Q9a）").font = H2_FONT
    span = ws.cell(row=r + 1, column=3,
                   value='=IF(OR(C{0}="",C{1}=""),"",C{1}-C{0})'.format(i9a, i9b))
    span.font = Font(name=FONT, size=11, bold=True, color="1F6F3D")
    span.fill = PatternFill("solid", fgColor=C_HEAD_AUTO)
    span.border = BORDER
    span.alignment = Alignment(horizontal="center")
    ws.cell(row=r + 1, column=5,
            value="跨度越大，说明爽感衰减越快——中间那些关在「没感觉」区间").font = NOTE_FONT

    ws.conditional_formatting.add(
        "D4:D4",
        CellIsRule(operator="greaterThan", formula=["1.3"],
                   fill=PatternFill("solid", fgColor="FFC7CE"),
                   font=Font(name=FONT, size=10, color="9C0006", bold=True)),
    )
    return ws


# Sheet ⑥ 编码字典
def build_dict(wb):
    ws = wb.create_sheet("⑥编码字典")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDE", (14, 22, 40, 16, 40)):
        ws.column_dimensions[col].width = w

    ws["A1"] = "编码字典 · 填表时对着看，这张不用填"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "原则：能编码的绝不自由填。开放题回收率高但不可统计。原文留在备注里，不参与统计。"
    ws["A2"].font = Font(name=FONT, size=9, italic=True, color="808080")

    r = 4

    def table(title, headers, rows):
        nonlocal r
        ws.cell(row=r, column=1, value=title).font = H2_FONT
        r += 1
        for i, h in enumerate(headers, start=1):
            style_header(ws, r, i, C_HEAD_CAU)
            ws.cell(row=r, column=i, value=h)
        r += 1
        for row in rows:
            for i, v in enumerate(row, start=1):
                c = ws.cell(row=r, column=i, value=v)
                c.font = BODY_FONT
                c.border = BORDER
                c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 24
            r += 1
        r += 1

    table("卡关原因编码（N列）", ["码", "归因", "修法方向"], [
        (0, "没卡住", "—"),
        (1, "资源不够", "调产出 / 调消耗（数值问题）"),
        (2, "概率不好（脸黑）", "加保底 / 降低方差（随机性问题）"),
        (3, "没看懂规则", "改引导 / 改 UI 提示（信息问题）"),
        (4, "操作繁琐", "减步骤 / 加一键（交互问题）"),
        (5, "不知道要干嘛", "改目标呈现（动机问题）"),
        (9, "其他", "记 9，并在备注留原文"),
    ])

    table("卡关选择编码（O列）", ["码", "选择", "含义"], [
        (0, "没卡住", "—"),
        (1, "继续等 / 继续攒", "容忍度高，或没有更好的出口"),
        (2, "看广告", "广告位自然触发"),
        (3, "想付费", "付费意愿信号（内部测试只能问意愿，不能真付费）"),
        (4, "想退出", "最强流失信号，等同准流失点"),
    ])

    table("难度增量（I列）", ["码", "含义", "说明"], [
        (-2, "容易很多", "相对上一关"),
        (-1, "容易一点", "相对上一关"),
        (0, "差不多", "相对上一关"),
        (1, "难一点", "相对上一关"),
        (2, "难很多", "相对上一关"),
    ])

    table("自动指标判读阈值", ["指标", "阈值", "说明"], [
        ("卡关占比", "> 0.4", "这关大部分时间在干等"),
        ("时间膨胀系数", "> 1.3", "体感比实际漫长（枯燥 / 干等）"),
        ("相邻关卡跳变比", "> 1.5", "断崖点，优先修"),
        ("无效操作率", "> 0.2", "操作费解或 UI 有坑"),
    ])
    return ws


def main():
    wb = Workbook()
    wb.remove(wb.active)
    build_intro(wb)
    build_meta(wb)
    build_main(wb)
    build_replay(wb)
    build_survey(wb)
    build_dict(wb)
    wb.properties.title = "PlayPulse 试玩采集表"
    wb.properties.creator = "PlayPulse"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wb.save(OUT)
    print("OK ->", OUT)


if __name__ == "__main__":
    main()
