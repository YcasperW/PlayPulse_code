"""分析卡片：按关键词把问题路由到预定义口径的结论。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .io_load import Dataset
from .semantics import Ontology, player_level_matrix, default_ontology
from . import statsx


@dataclass
class CardResult:
    """一张卡片跑完的结果。"""
    card_id: str
    title: str                       # 卡片标题
    headline: str                    # 一句话结论（人话，先给结论）
    table: Optional[pd.DataFrame] = None
    notes: List[str] = field(default_factory=list)   # 口径说明 / 可信边界
    chart: Optional[str] = None      # base64 PNG
    matched: bool = True             # False = 没匹配上任何卡片

    # 把卡片结果拼成纯文本，供 CLI 直接打印
    def render_text(self) -> str:
        out = ["【%s】%s" % (self.title, self.headline)]
        if self.table is not None and not self.table.empty:
            out.append(self.table.to_string(index=False))
        for n in self.notes:
            out.append("※ " + n)
        return "\n".join(out)


@dataclass
class Card:
    """一张分析卡片 = 一个策划会问的问题 + 一个绑死的口径。"""
    id: str
    name: str
    question: str                    # 卡片代表的人话问题
    keywords: List[str]              # 意图路由用的触发词
    why: str                         # 为什么这么答（口径依据，会展示给用户）
    run: Callable[["PulseCheck", Ontology], CardResult]

    def keyword_score(self, q: str) -> int:
        """关键词命中打分，只匹配字面，不做语义理解。"""
        s = 0
        for k in self.keywords:
            if k in q:
                s += len(k)          # 长词权重更高
        return s


# 按可信度生成结论后缀，样本不足时提示只作方向参考
def _verdict_suffix(verdict: str, n: int) -> str:
    if verdict == statsx.VERDICT_CONCLUSION:
        return ""
    if verdict == statsx.VERDICT_DIRECTION:
        return "（样本 %d 人，仅作方向参考，别据此改数值）" % n
    return "（样本 %d 人，不足以下判断）" % n


def _index_table(lm) -> pd.DataFrame:
    """各关难度指数表，按指数降序。"""
    rows = []
    for lv in sorted(lm.wide.index):
        d = lm.detail_at("difficulty_index", int(lv))
        rows.append({"关卡": int(lv), "难度指数": round(d.get("point", float("nan")), 1),
                     "样本(人)": d.get("n", 0), "可信度": d.get("verdict_label", "")})
    return pd.DataFrame(rows).sort_values("难度指数", ascending=False).reset_index(drop=True)


# 「哪关最难」：按难度指数排序，不是按耗时排序
def _card_hardest_level(pc, onto: Ontology) -> CardResult:
    lm = pc.level_metrics
    if "difficulty_index" not in lm.wide.columns:
        # 算不出来就老实说，绝不悄悄退化成「按耗时排序」
        return CardResult("hardest_level", "哪关最难",
                          "难度指数算不出来（缺少构成指标），本次答不了。",
                          notes=["需要 耗时 / 卡关占比 / 尝试次数 / 挫败感 中的至少一项，"
                                 "补充数据后重跑即可。"])
    t = _index_table(lm)
    top = t.iloc[0]
    v = lm.detail_at("difficulty_index", int(top["关卡"])).get("verdict", "")
    head = "第 %d 关最难（难度指数 %.1f），其次第 %d 关。%s" % (
        int(top["关卡"]), top["难度指数"],
        int(t.iloc[1]["关卡"]) if len(t) > 1 else int(top["关卡"]),
        _verdict_suffix(v, int(top["样本(人)"])))
    return CardResult("hardest_level", "哪关最难", head, t,
                      notes=["口径是**难度指数**，不是「按耗时排序」：耗时最长 ≠ 最难——"
                             "一关耗时长但很顺（在攒资源），另一关耗时短但反复失败，后者才是真难。",
                             "难度指数 = 耗时 0.35 + 卡关占比 0.30 + 尝试次数 0.20 + 挫败感 0.15，"
                             "归一化到 0-100。权重在 metrics.yaml，改了全项目生效。"])


# 「哪关最顺」，并提示难度指数过低可能是没内容而非体验好
def _card_easiest_level(pc, onto: Ontology) -> CardResult:
    lm = pc.level_metrics
    if "difficulty_index" not in lm.wide.columns:
        return CardResult("easiest_level", "哪关最顺", "难度指数算不出来，本次答不了。")
    t = _index_table(lm).iloc[::-1].reset_index(drop=True)
    top = t.iloc[0]
    head = "第 %d 关最顺（难度指数 %.1f）。" % (int(top["关卡"]), top["难度指数"])
    if float(top["难度指数"]) < 15:
        head += "顺到几乎没阻力——要确认是「设计得好」还是「没内容」。"
    return CardResult("easiest_level", "哪关最顺", head, t,
                      notes=["难度指数低不等于体验好：如果这关同时耗时最短、感知也低，"
                             "更可能是玩家在无感消耗（无聊），不是心流。"])


# 难度曲线形状评估，附逐关相对上一关的倍率
def _card_curve(pc, onto: Ontology) -> CardResult:
    c = pc.curve or {}
    shape = c.get("shape", "未知")
    comment = c.get("comment", "")
    rows = []
    lm = pc.level_metrics
    for lv in sorted(lm.wide.index):
        d = lm.detail_at("duration_sec", int(lv))
        rows.append({"关卡": int(lv), "中位耗时(秒)": round(d.get("point", float("nan")), 1),
                     "相对上一关": ("" if int(lv) == sorted(lm.wide.index)[0]
                                 else "×%.2f" % (d.get("point", 0) /
                                                 max(lm.value_at("duration_sec", int(lv) - 1), 1e-9)))})
    return CardResult("curve_shape", "难度曲线形状",
                      "曲线形状：**%s**。%s" % (shape, comment),
                      pd.DataFrame(rows),
                      notes=["期望形状是「锯齿上升」——有起伏有喘息。"
                             "直线上升会让玩家看不到头，长期平台会让人走神。"])


# 通关总时长估算：各关中位数相加，不是总时长中位数
def _card_playtime(pc, onto: Ontology) -> CardResult:
    lm = pc.level_metrics
    rows = []
    total = 0.0
    for lv in sorted(lm.wide.index):
        d = lm.detail_at("duration_sec", int(lv))
        p = d.get("point", float("nan"))
        lo, hi = d.get("ci_low", float("nan")), d.get("ci_high", float("nan"))
        total += p if np.isfinite(p) else 0
        rows.append({"关卡": int(lv), "中位耗时(秒)": round(p, 1),
                     "95%CI": "[%.0f, %.0f]" % (lo, hi) if np.isfinite(lo) else "—",
                     "折合分钟": round(p / 60.0, 1)})
    t = pd.DataFrame(rows)
    head = ("通关总时长中位数约 **%.1f 分钟**（%d 关合计，按各关中位数相加）。"
            % (total / 60.0, len(rows)))
    n = pc.n_players
    head += _verdict_suffix(statsx.verdict_by_sample_size(n, 8), n)
    return CardResult("playtime_estimate", "一次通关要多久", head, t,
                      notes=["这是**各关中位数相加**，不是「所有人总时长的中位数」——"
                             "后者需要同一个玩家打完所有关且记录完整，小样本下前者更稳。",
                             "如果策划自己就是玩家之一，这个数是**下限**："
                             "策划知道解法，真实新手会更慢。"])


# 卡关归因分布，并按首位原因提示该改引导还是改数值
def _card_stuck_reason(pc, onto: Ontology) -> CardResult:
    t = pc.attribution
    if t is None or t.empty:
        return CardResult("stuck_reason", "为什么会卡住",
                          "没有填卡关原因，本次答不了。",
                          notes=["卡关归因来自 stuck_reason_code（0-5/9），"
                                 "见 templates/编码字典-文本转数字.md。"])
    overall = pc.attribution_overall
    top = overall.iloc[0] if overall is not None and not overall.empty else None
    head = ("最主要的卡关原因是「**%s**」，占 %.0f%%。%s"
            % (top["原因"], top["占比"] * 100,
               "这是信息问题（引导没说清），改引导，别动数值。"
               if "不知道" in str(top["原因"]) or "看不懂" in str(top["原因"])
               else "先回看录屏确认归因，再决定动引导还是动数值。")
            if top is not None else "无数据")
    return CardResult("stuck_reason", "为什么会卡住", head, t,
                      notes=["归因错了就会改错地方："
                             "「不会玩」是信息问题，「打不过」才是数值问题。"])


# 卡住时三选一的分布，退出占比过高的关即准流失点
def _card_stuck_choice(pc, onto: Ontology) -> CardResult:
    t = pc.choice_dist
    if t is None or t.empty:
        return CardResult("stuck_choice", "卡住时玩家选什么",
                          "没有卡关选择数据，本次答不了。",
                          notes=["卡关选择来自强制选择题 stuck_choice_code（0-4）。"
                                 "注意：必须问「你会怎么做」，不能问「你愿意付费吗」——"
                                 "后者是无效意愿提问。"])
    return CardResult("stuck_choice", "卡住时玩家选什么",
                      "见下表（按关卡分布）。「直接退出」占比 > 20% 的关卡即准流失点。",
                      t,
                      notes=["这个指标的价值在于它是**被迫选择**：玩家真的卡住了才会选看广告/付费，"
                             "所以它能反推广告位该放哪、数值墙有多硬。"])


def _card_segment(pc, onto: Ontology) -> CardResult:
    """新手 vs 老手对比；小样本下两组差异容易只是噪声，判据从严。"""
    ds = pc.dataset
    if not ds.has_fields("player_type"):
        return CardResult("segment_compare", "新手和老手差多少",
                          "数据里没有玩家水平字段（player_type），本次答不了。",
                          notes=["现场填写表的「玩家水平」下拉（新手/老手）会映射到该字段。"])

    # 按玩家水平分层拆出 (玩家 × 关卡) 矩阵。分层后每组样本量腰斩，
    # 可信度必须逐组重判，不能沿用整体的判定
    mats: Dict[str, pd.DataFrame] = {}
    for g, sub in ds.df.groupby(ds.df["player_type"].astype(str)):
        if str(g).strip() in ("", "nan", "None"):
            continue
        sub_ds = Dataset(df=sub.reset_index(drop=True), issues=[], column_map={},
                         unmapped=[], sources=ds.sources)
        try:
            mats[str(g)] = player_level_matrix(sub_ds, onto, "duration_sec")
        except Exception:
            continue

    if len(mats) < 2:
        return CardResult("segment_compare", "新手和老手差多少",
                          "只识别出一个玩家分层，无法对比。")

    rows = []
    names = sorted(mats.keys())
    for lv in sorted(set().union(*[set(m.columns) for m in mats.values()])):
        a = mats[names[0]].get(lv)
        b = mats[names[1]].get(lv)
        if a is None or b is None:
            continue
        a = pd.to_numeric(a, errors="coerce").dropna()
        b = pd.to_numeric(b, errors="coerce").dropna()
        if len(a) < 3 or len(b) < 3:
            rows.append({"关卡": int(lv), names[0]: "样本不足", names[1]: "样本不足",
                         "差异": "—"})
            continue
        r = statsx.compare_two_groups(a.values, b.values, min_n=8)
        rows.append({
            "关卡": int(lv),
            "%s 中位(秒)" % names[0]: round(float(np.median(a)), 1),
            "%s 中位(秒)" % names[1]: round(float(np.median(b)), 1),
            "差异(秒)": round(float(np.median(b) - np.median(a)), 1),
            "效应量 d": round(float(r.get("cohens_d", float("nan"))), 2),
            "是否超出噪声": "是" if r.get("beyond_noise") else "否（可能只是波动）",
            "可信度": r.get("verdict_label", ""),
        })
    t = pd.DataFrame(rows)

    noisy = [r for r in rows if r.get("是否超出噪声", "").startswith("否")]
    head = ("新手与老手的耗时差异：%d/%d 关超出噪声范围。"
            % (len(rows) - len(noisy), len(rows)))
    if len(noisy) > len(rows) / 2:
        head += "**多数关卡的差异没超出噪声，不要据此分别调难度。**"

    return CardResult("segment_compare", "新手和老手差多少", head, t,
                      notes=["分层后每组样本量会腰斩，可信度必须**逐组重判**，"
                             "不能沿用 100 人时的判定。",
                             "如果两组差异很小，通常不是「难度设计得好」，"
                             "而是老手也不知道最优解（游戏还没被玩透）。"])


# 汇总断崖、硬卡点、准流失三类流失风险
def _card_drop_risk(pc, onto: Ontology) -> CardResult:
    rows = []
    for f in pc.findings:
        if f.rule_id in ("quit_alert", "cliff", "hard_block"):
            rows.append({"关卡": f.level if f.level is not None else "—",
                         "风险": f.name, "程度": f.severity,
                         "结论": re.sub(r"\*\*", "", f.conclusion),
                         "建议": f.advice or "—"})
    if not rows:
        return CardResult("drop_risk", "哪关在赶人",
                          "本次没有检测到强流失信号（断崖 / 硬卡点 / 准流失）。",
                          notes=["不代表真的没问题——也可能是样本量不足以触发判定。"
                                 "当前 %d 人。" % pc.n_players])
    t = pd.DataFrame(rows)
    return CardResult("drop_risk", "哪关在赶人",
                      "共 %d 处流失风险，最严重的在下面表格第一行。" % len(rows), t)


# 客观难度 vs 感知难度四象限，识别假性难度
def _card_perception(pc, onto: Ontology) -> CardResult:
    q = pc.quadrants
    if q is None or q.empty:
        return CardResult("perception_gap", "玩家觉得难还是真的难",
                          "缺感知难度数据，本次答不了。",
                          notes=["需要 estimated_duration_sec（玩家估计自己花了多久）"
                                 "和 difficulty_delta（觉得比上一关难/易多少）。"])
    return CardResult("perception_gap", "玩家觉得难还是真的难",
                      "见四象限表。「客观不难但玩家觉得难」= 假性难度，"
                      "改进度反馈而不是降数值。", q,
                      notes=["四象限按中位数切分，不是按绝对阈值——"
                             "关卡少的时候中位数切分比绝对阈值稳健。"])


def _card_trust(pc, onto: Ontology) -> CardResult:
    """「这个结论我能信吗」——这张卡片本身就是本项目的立场声明。"""
    n = pc.n_players
    rows = [{
        "检查项": "样本量单位", "结果": "%d 人" % n,
        "说明": "置信区间按玩家重抽样，**不是按行数**。%d 行数据不等于 %d 个样本。"
                % (pc.dataset.n_rows, pc.dataset.n_rows)},
        {"检查项": "可信结论数",
         "结果": "%d / %d" % (sum(1 for f in pc.findings if f.is_trustworthy), len(pc.findings)),
         "说明": "不可信的结论已在报告中标注为「仅供方向参考」"},
        {"检查项": "可计算指标",
         "结果": "%d / %d" % (len([m for m in pc.level_metrics.meta if m in pc.level_metrics.wide.columns]),
                              len(pc.level_metrics.meta)),
         "说明": "算不了的指标会显式列出原因，不会默默跳过"},
        {"检查项": "是否含合成数据",
         "结果": "是" if pc.dataset.is_synthetic else "否",
         "说明": "含合成数据时结论只能用于验证系统，不能当真实结论"},
    ]
    t = pd.DataFrame(rows)
    head = ("%d 人样本：**%s**。"
            % (n, "可以下结论" if n >= 8 else "只够看方向，不够下结论"))
    return CardResult("trust_check", "这个结论我能信吗", head, t,
                      notes=["判定门槛：≥8 人可下结论，5-7 人只给方向，<5 人拒绝判断。"
                             "门槛写在 metrics.yaml 的 thresholds 里。",
                             "用 Bootstrap 百分位法而不是 t 检验，因为试玩时长是右偏的"
                             "（少数人拖很久），t 检验在小样本下会严重低估不确定性。"])


# 列出全部指标的口径公式，供业务与数据双方对齐
def _card_glossary(pc, onto: Ontology) -> CardResult:
    rows = []
    for mid, m in onto.metrics.items():
        rows.append({"指标": m.name, "口径（公式）": m.formula,
                     "聚合": m.agg, "单位": m.unit or "—",
                     "为什么这么算": (m.why or "").replace("\n", " ")[:120]})
    return CardResult("glossary", "这些指标都是怎么算的",
                      "当前本体共 %d 个指标，口径全部来自 metrics.yaml（可版本化、可导出）。"
                      % len(rows), pd.DataFrame(rows),
                      notes=["这份表就是给业务和数据对齐用的——"
                             "**口径只有这一个来源**，不存在两个人算出两个数的情况。"])


CARDS: List[Card] = [
    Card("hardest_level", "哪关最难", "哪关最难？",
         ["哪关最难", "最难", "最难的关", "难度最高", "哪关劝退"],
         "难度指数，非耗时排序", _card_hardest_level),
    Card("easiest_level", "哪关最顺", "哪关最顺？",
         ["哪关最顺", "最顺", "最简单", "最easy", "哪关最容易"],
         "难度指数最低", _card_easiest_level),
    Card("curve_shape", "难度曲线", "难度曲线长什么样？",
         ["曲线", "节奏", "曲线形状", "起伏", "是不是太顺", "有没有起伏"],
         "锯齿上升为佳", _card_curve),
    Card("playtime_estimate", "通关时长", "一次通关要多久？",
         ["多久", "多长", "时长", "通关时间", "游玩时长", "几分钟", "多少分钟"],
         "各关中位数相加", _card_playtime),
    Card("stuck_reason", "卡关归因", "玩家为什么会卡住？",
         ["为什么卡", "卡关原因", "归因", "卡在哪", "为什么过不去"],
         "stuck_reason_code 分布", _card_stuck_reason),
    Card("stuck_choice", "卡关选择", "卡住时玩家会做什么？",
         ["卡住", "选择", "看广告", "付费", "退出", "流失", "卡关选择"],
         "stuck_choice_code 分布", _card_stuck_choice),
    Card("segment_compare", "新手vs老手", "新手和老手差多少？",
         ["新手", "老手", "分层", "水平", "差异", "对比"],
         "分组重判可信度", _card_segment),
    Card("drop_risk", "流失风险", "哪关在赶人？",
         ["赶人", "流失", "劝退", "哪里有问题", "风险"],
         "断崖/硬卡点/准流失", _card_drop_risk),
    Card("perception_gap", "感知背离", "是玩家觉得难还是真的难？",
         ["感知", "体感", "觉得难", "主观", "四象限", "背离"],
         "客观×感知四象限", _card_perception),
    Card("trust_check", "可信度", "这个结论我能信吗？",
         ["可信", "能信吗", "样本", "多少人才够", "统计", "置信"],
         "样本量为玩家数", _card_trust),
    Card("glossary", "口径查询", "这些指标都是怎么算的？",
         ["口径", "怎么算", "定义", "指标", "公式", "文档"],
         "metrics.yaml 单一来源", _card_glossary),
]

CARD_BY_ID: Dict[str, Card] = {c.id: c for c in CARDS}


def route_intent(question: str) -> Optional[Card]:
    """把一句话路由到一张卡片。只做关键词匹配，不生成 SQL 或代码。"""
    q = (question or "").strip()
    if not q:
        return None
    # 必须按分数取 max：直接比 (score, Card) 会在并列时去比 Card，而它不可比较
    score, card = max(((c.keyword_score(q), c) for c in CARDS), key=lambda x: x[0])
    return card if score else None


def fallback_card(question: str) -> CardResult:
    """匹配不上时的兜底。"""
    lines = ["这个问题我没找到对应的分析卡片，所以**不会给你一个口径不明的数字**。",
             "", "当前能直接回答的问题："]
    for c in CARDS:
        lines.append("- **%s**（%s）—— 试试问：%s" % (c.name, c.why, c.question))
    lines += ["", "如果你的问题不在上面，正确做法是：",
              "1. 先确认你要的指标在不在 metrics.yaml 里；",
              "2. 不在的话，把它作为一条新指标写进 YAML（顺手就把口径定死了）；",
              "3. 再给它配一张卡片。这样下次全项目都能问这个问题，口径还不会跑偏。"]
    return CardResult("fallback", "答不了这个问题",
                      "没有匹配到分析卡片：「%s」" % (question or ""),
                      None, notes=lines, matched=False)


def ask(pc, question: str, onto: Optional[Ontology] = None) -> CardResult:
    """问答入口：问一句人话 → 返回一张卡片的结论。"""
    onto = onto or getattr(pc, "ontology", None) or default_ontology()
    card = route_intent(question)
    if card is None:
        return fallback_card(question)
    try:
        return card.run(pc, onto)
    except Exception as e:
        # 卡片跑挂了也要说实话，不能给半截结果
        return CardResult(card.id, card.name,
                          "这张卡片本次跑失败了：%s" % e,
                          notes=["通常是缺少某个字段。看报告最后的「数据体检」"
                                 "章节，那里列了缺什么、会影响哪些指标。"])
