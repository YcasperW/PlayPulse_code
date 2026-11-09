"""
PlayPulse · 合成试玩数据生成器

用途：
    现有真实数据只有 2 份（新手 1 份 / 老手 1 份，各 3 关），不足以跑通分析链路。
    本脚本按现有样例反推出的分布参数，为每个类别再生成 50 份仿真试玩记录。

产出（样本表 2026-11-06 已归档到 _archive，不再堆在 templates/ 里）：
    _archive/2026-11-06-归档噪音/试玩样本_新手/试玩采集表_新手2.xlsx ... _新手51.xlsx
    _archive/2026-11-06-归档噪音/试玩样本_老手/试玩采集表_老手2.xlsx ... _老手51.xlsx
    data/raw/试玩数据-合成-100份.csv                          （汇总长表，供入库/分析）

用法：
    python scripts/gen_synthetic_playtests.py

改参数：
    LEVELS_PER_FILE  每份文件的关卡数（默认 20）
    N_FILES          每个类别生成多少份（默认 50）
    SEED             随机种子，固定即可复现

建模依据（全部来自现有 2 份真实样例，见 PROFILE 常量）：
    游戏只有 3 关，因此不做线性外推，直接把真实样例的逐关观测值当作分布中位数
    （耗时 / 卡关占比 / 尝试次数 / 挫败感 / 挑战感 / 预估耗时比），再叠加噪声。
    老手预估偏乐观或接近实际（膨胀 0.97~1.33），新手系统性低估自己花的时间（0.59~0.82）。
    难度增量以 +1 为主，新手后段出现 +2（断崖）。
    超出已校准关卡数时用最后一段斜率线性外推（当前用不到，作为兜底）。

个体异质性（关键：避免"每个新手都一样菜、每个老手都一样强"）：
    每位玩家先抽一个漂移系数 lam，表示他落在「本类中位」与「对面类中位」之间的位置：
        lam = 0    正好是本类的典型水平
        lam = 1    完全变成对面类的典型水平
        lam < 0    比本类典型更典型（更强的新手 / 更菜的老手）
    普通个体 lam ~ N(0, LAM_SIGMA)，截断在 LAM_CLAMP 内；
    离群个体 lam ~ U(OUTLIER_LAM_RANGE)，即"新手里玩得好的 / 老手里玩得菜的"，
    每类随机抽 OUTLIER_COUNT_RANGE 个。
    逐关目标值一律在 own / cross 之间按 lam 线性插值；lam 超过 CROSS_SWITCH_LAM 时，
    主观分布（难度感知、卡关原因、卡关选择）也切成对面类别的——玩得好的人归因方式也更像老手。

诚信约定：
    生成的是**仿真数据**，不是真实玩家数据。汇总 CSV 带 is_synthetic=1 列，
    对外演示 / 简历 / 面试必须标注为仿真数据。
"""

import os
import random
import csv
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.formatting.rule import CellIsRule

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(BASE, "_archive", "2026-11-06-归档噪音")
OUT_DIR = {"新手": os.path.join(ARCHIVE, "试玩样本_新手"),
           "老手": os.path.join(ARCHIVE, "试玩样本_老手")}

# 模板来源：优先 templates/试玩采集表.xlsx；若已被移走，回退到归档里的真实样例
_TPL_CANDIDATES = [
    os.path.join(BASE, "templates", "试玩采集表.xlsx"),
    os.path.join(ARCHIVE, "试玩样本_新手", "试玩采集表_新手1.xlsx"),
    os.path.join(ARCHIVE, "试玩样本_老手", "试玩采集表_老手1.xlsx"),
]
TEMPLATE = next((p for p in _TPL_CANDIDATES if os.path.exists(p)), None)
if TEMPLATE is None:
    raise FileNotFoundError("找不到模板文件，请先运行 scripts/make_playtest_sheet.py")
SUMMARY_CSV = os.path.join(BASE, "data", "raw", "试玩数据-合成-100份.csv")

LEVELS_PER_FILE = 3      # 每份文件的关卡数（当前游戏只有 3 关）
N_FILES = 50             # 每个类别生成多少份
SEED = 20260921

# 个体异质性
# lam：0 = 本类典型水平，1 = 对面类典型水平，<0 = 比本类典型更典型
LAM_SIGMA = 0.10                  # 普通个体的漂移强度
LAM_CLAMP = (-0.25, 0.25)         # 普通个体的漂移范围
OUTLIER_COUNT_RANGE = (3, 7)      # 每类抽几个离群个体（新手里的高手 / 老手里的菜鸟）
# 离群个体向对面类迁移的比例。上限不取 1.0：否则"新手高手"会快过老手中位数、
# "老手菜鸟"会慢过新手中位数，把两个类别本身的区分度抹掉。0.80 时刚好落在
# 对面类的中后段（新手高手 ≈ 老手中下水平，老手菜鸟 ≈ 新手中上水平），有交叉但不翻转。
OUTLIER_LAM_RANGE = (0.55, 0.80)
CROSS_SWITCH_LAM = 0.45           # lam 超过此值时，主观分布也改用对面类别

# 表格布局（与 make_playtest_sheet.py 保持一致）
META_ROW = 3
HEAD_ROW = 5
FIRST_ROW = 6

# 分布参数：新手 / 老手
#
# 游戏只有 3 关，所以不做公式外推：下面 *_tgt 三个数就是真实样例的逐关观测值，
# 生成时把它们当作分布中位数，再叠加噪声。这样合成数据与真实样例逐关对齐。
#
#   真实老手：耗时 15/31/42  卡关占比 0/.16/.19  尝试 1/4/6  挫败 1/2/3  挑战 1/2/4  预估 20/30/50
#   真实新手：耗时 32/51/73  卡关占比 .31/.49/.55 尝试 3/8/14 挫败 1/3/3  挑战 2/3/4  预估 20/30/60
#
#   dur_tgt        耗时中位数（秒）
#   stuck_tgt      卡关占比中位数
#   att_tgt        尝试次数中位数（1 = 一次过）
#   fru/chal_tgt   挫败感 / 挑战感中位数（1-5）
#   est_tgt        预估耗时 ÷ 实际耗时 的中位数（<1 = 低估自己花了多久）
#   *_sigma        噪声强度
PROFILE = {
    "老手": dict(
        dur_tgt=[15, 31, 42],
        stuck_tgt=[0.00, 0.16, 0.19],
        att_tgt=[1, 4, 6],
        fru_tgt=[1, 2, 3],
        chal_tgt=[1, 2, 4],
        est_tgt=[1.33, 0.97, 1.19],
        dur_sigma=0.18, stuck_sigma=0.05, att_sigma=0.55,
        fru_sigma=0.55, chal_sigma=0.50, est_sigma=0.12,
        stuck_max=0.60, att_max=18,
        diff_w={1: 60, 0: 25, 2: 10, -1: 5},
        cause_w={2: 30, 4: 25, 1: 20, 5: 10, 3: 10, 9: 5},
        choice_w={1: 70, 2: 12, 3: 10, 4: 8},
        code="E",
    ),
    "新手": dict(
        dur_tgt=[32, 51, 73],
        stuck_tgt=[0.31, 0.49, 0.55],
        att_tgt=[3, 8, 14],
        fru_tgt=[1, 3, 3],
        chal_tgt=[2, 3, 4],
        est_tgt=[0.63, 0.59, 0.82],
        dur_sigma=0.20, stuck_sigma=0.06, att_sigma=0.90,
        fru_sigma=0.55, chal_sigma=0.50, est_sigma=0.12,
        stuck_max=0.75, att_max=28,
        diff_w={1: 50, 2: 30, 0: 15, -1: 5},
        cause_w={5: 35, 3: 25, 1: 20, 4: 10, 2: 5, 9: 5},
        choice_w={1: 55, 2: 20, 3: 10, 4: 15},
        code="N",
    ),
}

NOTES = ["", "", "", "", "", "", "", "卡在信号重叠处", "没看懂提示", "手滑重来", "想跳过"]
CAUSE9_NOTE = {9: "其他规则没搞明白"}


def wchoice(rng, weights):
    """按 {值: 权重} 抽样"""
    keys = list(weights.keys())
    ws = [weights[k] for k in keys]
    return rng.choices(keys, weights=ws, k=1)[0]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def at_tgt(seq, i):
    """取第 i 关（1-based）的目标值。

    已校准范围内直接取真实样例观测值；超出则用最后一段斜率线性外推（兜底）。
    """
    if i <= len(seq):
        return seq[i - 1]
    step = seq[-1] - seq[-2]
    return seq[-1] + step * (i - len(seq))


def player_targets(rng, cat, is_outlier=False):
    """按玩家的漂移系数 lam，把「本类」与「对面类」的逐关目标值插值成该玩家的目标。

    lam = 0    本类的典型水平（普通玩家）
    lam = 1    对面类的典型水平（新手里的高手 / 老手里的菜鸟）
    lam < 0    比本类典型更典型（更强的新手 / 更菜的老手）

    个体差异落在「目标值」上而不是噪声上，所以同一位玩家三关会一致地偏快或偏慢。
    """
    own = PROFILE[cat]
    cross = PROFILE["老手" if cat == "新手" else "新手"]

    lam = rng.uniform(*OUTLIER_LAM_RANGE) if is_outlier \
        else clamp(rng.gauss(0, LAM_SIGMA), *LAM_CLAMP)

    def bl(a, b):
        return a + (b - a) * lam

    t = {"lam": lam}
    for k in ("dur", "stuck", "att", "fru", "chal", "est"):
        t[k] = [bl(o, c) for o, c in zip(own[k + "_tgt"], cross[k + "_tgt"])]

    # 主观分布：漂移足够大时，这个人的归因/感知方式也更像对面类别
    src = cross if lam >= CROSS_SWITCH_LAM else own
    t["diff_w"] = src["diff_w"]
    t["cause_w"] = src["cause_w"]
    t["choice_w"] = src["choice_w"]

    t["stuck_max"] = clamp(bl(own["stuck_max"], cross["stuck_max"]), 0.25, 0.85)
    t["att_max"] = int(max(4, round(bl(own["att_max"], cross["att_max"]))))
    return t


def gen_one_player(rng, cat, n_levels, t=None):
    """生成一位玩家的完整试玩记录，返回 list[dict]"""
    p = PROFILE[cat]
    t = t if t is not None else player_targets(rng, cat)
    rows = []
    for i in range(1, n_levels + 1):
        # 耗时：该玩家的目标值作中位数 × 对数正态噪声
        dur = int(clamp(round(at_tgt(t["dur"], i)
                              * rng.lognormvariate(0, p["dur_sigma"])), 8, 900))

        # 卡关占比
        stuck = clamp(at_tgt(t["stuck"], i) + rng.gauss(0, p["stuck_sigma"]),
                      0.0, t["stuck_max"])
        stuck_t = int(round(dur * stuck))
        stuck_t = min(stuck_t, dur)
        ratio = round(stuck_t / dur, 2) if dur else 0.0

        # 尝试次数
        att = int(clamp(round(at_tgt(t["att"], i)
                              + rng.gauss(0, p["att_sigma"])), 1, t["att_max"]))

        # 难度 vs 上一关：首关固定 0
        diff = 0 if i == 1 else wchoice(rng, t["diff_w"])

        # 挫败感 / 挑战感
        fru = int(clamp(round(at_tgt(t["fru"], i) + rng.gauss(0, p["fru_sigma"])), 1, 5))
        chal = int(clamp(round(at_tgt(t["chal"], i) + rng.gauss(0, p["chal_sigma"])), 1, 5))

        # 预估耗时：该玩家的膨胀比作中位数，取整到 10 秒
        est_r = clamp(at_tgt(t["est"], i) + rng.gauss(0, p["est_sigma"]), 0.4, 2.0)
        est = max(10, int(round(dur * est_r / 10.0)) * 10)
        infl = round(est / dur, 2) if dur else 0.0

        # 卡关归因（没卡住就一律填 0，不留备注）
        if stuck < 0.08:
            cause, choice, note = 0, 0, None
        else:
            cause = wchoice(rng, t["cause_w"])
            # 挫败越高越可能想退出
            cw = dict(t["choice_w"])
            cw[4] = cw.get(4, 0) + int((fru - 2) * 8)
            choice = wchoice(rng, cw)
            # 备注：卡关原因选 9 时写原文；其余大概率留空，偶尔随手写一句
            note = CAUSE9_NOTE.get(cause, rng.choice(NOTES))

        rows.append(dict(
            level=i, dur=dur, stuck_t=stuck_t, ratio=ratio,
            att=att, diff=diff, fru=fru, chal=chal,
            est=est, infl=infl, cause=cause, choice=choice, note=note,
        ))
    return rows


# 下拉与标红规则（模板在被外部编辑器另存后范围可能被压缩，这里强制重建为完整区间）
DV_RULES = [("F", "-2,-1,0,1,2"), ("G", "1,2,3,4,5"), ("H", "1,2,3,4,5"),
            ("K", "0,1,2,3,4,5,9"), ("L", "0,1,2,3,4")]
CF_RULES = [("D", "0.4"), ("J", "1.3")]


def rebuild_rules(ws, first, last):
    ws.data_validations.dataValidation.clear()
    ws.conditional_formatting = ConditionalFormattingList()

    dv = DataValidation(type="list", formula1='"老手,新手"', allow_blank=True, showDropDown=False)
    ws.add_data_validation(dv)
    dv.add("B%d" % META_ROW)

    for col, opts in DV_RULES:
        d = DataValidation(type="list", formula1='"%s"' % opts, allow_blank=True, showDropDown=False)
        ws.add_data_validation(d)
        d.add("%s%d:%s%d" % (col, first, col, last))

    for col, thr in CF_RULES:
        ws.conditional_formatting.add(
            "%s%d:%s%d" % (col, first, col, last),
            CellIsRule(operator="greaterThan", formula=[thr],
                       fill=PatternFill("solid", fgColor="FFC7CE"),
                       font=Font(name="微软雅黑", size=10, color="9C0006", bold=True)),
        )


def fill_workbook(tpl_path, out_path, cat, pid, version, date, rows):
    wb = load_workbook(tpl_path)
    ws = wb["①现场填写表"]

    ws.cell(row=META_ROW, column=1, value=pid)
    ws.cell(row=META_ROW, column=2, value=cat)
    ws.cell(row=META_ROW, column=3, value=version)
    ws.cell(row=META_ROW, column=4, value=date)

    # 模板底部的提示行（关卡只有 3 关时它落在数据区下方不远处，先取出来）
    # 注意：openpyxl 的 ws.cell(..., value=None) 不会清空已有值，必须显式赋值
    tail = [(r, ws.cell(row=r, column=1).value)
            for r in range(FIRST_ROW, FIRST_ROW + 70)
            if isinstance(ws.cell(row=r, column=1).value, str)
            and ws.cell(row=r, column=1).value.startswith(("绿色两列", "最少只填"))]

    # 清空数据区残留（模板可能来自已填过的样例文件）
    for row in range(FIRST_ROW, FIRST_ROW + 60):
        for c in range(1, 14):
            ws.cell(row=row, column=c).value = None

    for i, r in enumerate(rows):
        row = FIRST_ROW + i
        vals = [r["level"], r["dur"], r["stuck_t"], r["ratio"], r["att"], r["diff"],
                r["fru"], r["chal"], r["est"], r["infl"], r["cause"], r["choice"], r["note"]]
        for c, v in enumerate(vals, start=1):
            ws.cell(row=row, column=c, value=v)

    for trow, tval in tail:          # 写回底部提示
        ws.cell(row=trow, column=1).value = tval

    rebuild_rules(ws, FIRST_ROW, FIRST_ROW + len(rows) - 1)
    wb.save(out_path)


def main():
    rng = random.Random(SEED)
    os.makedirs(os.path.join(BASE, "data", "raw"), exist_ok=True)

    all_rows = []
    for cat in ("新手", "老手"):
        d = OUT_DIR[cat]
        os.makedirs(d, exist_ok=True)
        code = PROFILE[cat]["code"]

        # 随机挑几个离群个体（序号打散，不集中排在前面）
        n_out = rng.randint(*OUTLIER_COUNT_RANGE)
        outlier_idx = set(rng.sample(range(N_FILES), n_out))

        for k in range(1, N_FILES + 1):
            seq = k + 1                       # 现有第 1 份保留不动，从 2 开始
            is_out = (k - 1) in outlier_idx
            pid = "%s%02d" % (code, seq)
            date = "2026-09-%02d" % rng.randint(15, 21)
            t = player_targets(rng, cat, is_out)
            rows = gen_one_player(rng, cat, LEVELS_PER_FILE, t)
            out = os.path.join(d, "试玩采集表_%s%d.xlsx" % (cat, seq))
            fill_workbook(TEMPLATE, out, cat, pid, "V1", date, rows)
            for r in rows:
                all_rows.append(dict(pid=pid, cat=cat, version="V1", date=date,
                                     out=int(is_out), lam=round(t["lam"], 2), **r))

        # 打印离群个体，便于核对
        tag = "高手(接近老手)" if cat == "新手" else "菜鸟(接近新手)"
        pids = sorted("%s%02d" % (code, i + 2) for i in outlier_idx)
        print("%s: %d 份 -> %s | 离群 %d 个(%s): %s"
              % (cat, N_FILES, d, n_out, tag, ", ".join(pids)))

    # 汇总长表
    fields = ["pid", "cat", "version", "date", "level", "dur", "stuck_t", "ratio",
              "att", "diff", "fru", "chal", "est", "infl", "cause", "choice", "note",
              "out", "lam"]
    header = ["玩家编号", "玩家水平", "游戏版本", "日期", "关卡", "耗时(秒)", "卡关时长(秒)",
              "卡关占比", "尝试次数", "难度vs上一关", "挫败感", "挑战感", "预估耗时(秒)",
              "时间膨胀系数", "卡关原因", "卡关选择", "备注",
              "离群个体", "个体漂移λ", "is_synthetic"]
    with open(SUMMARY_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in all_rows:
            w.writerow([r[k] for k in fields] + [1])
    print("汇总 ->", SUMMARY_CSV, "| 行数:", len(all_rows))


if __name__ == "__main__":
    main()
