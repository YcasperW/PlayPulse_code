"""规范化数据模型：字段定义与列名别名表。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


GROUPS: Dict[str, str] = {
    "identity": "身份维度",
    "objective": "客观行为",
    "progress": "进度与尝试",
    "economy": "资源经济",
    "behavior": "行为数字（录屏回看）",
    "subjective": "主观感知",
    "attribution": "卡关归因",
    "outcome": "结果代理指标",
    "meta": "元数据",
}


@dataclass(frozen=True)
class FieldSpec:
    """一个规范字段的定义。"""

    name: str
    dtype: str
    group: str
    desc: str
    unit: str = ""
    layer: str = "universal"
    required: bool = False
    rng: Optional[tuple] = None
    note: str = ""


FIELDS: List[FieldSpec] = [
    # 身份维度
    FieldSpec("player_id", "str", "identity", "玩家编号，同一个人多次测试用同一个编号",
              required=True, note="缺失时按文件名兜底生成"),
    FieldSpec("session_id", "str", "identity", "一次游玩会话的编号",
              note="缺失时用 玩家编号+版本 兜底"),
    FieldSpec("player_type", "str", "identity", "玩家水平：老手 / 新手",
              layer="universal", note="老手=策划本人/同事/玩过同品类；新手=完全没玩过"),
    FieldSpec("is_planning_staff", "int", "identity", "是否策划本人（1=是）", rng=(0, 1)),
    FieldSpec("build_version", "str", "identity", "游戏版本号。每次调参必须换号，否则做不了版本对比",
              required=True, note="版本对比 Diff 的唯一依据"),
    FieldSpec("test_date", "str", "identity", "测试日期"),

    # 客观行为
    FieldSpec("level_id", "int", "objective", "关卡序号", required=True, rng=(1, 9999)),
    FieldSpec("duration_sec", "int", "objective", "关卡耗时：进入关卡到通过的总时长",
              unit="秒", required=True, rng=(1, 7200)),
    FieldSpec("active_sec", "int", "objective", "主动推进时长：有操作、有反馈的时间",
              unit="秒", rng=(0, 7200), note="缺失时用 耗时-卡关时长 兜底"),
    FieldSpec("stuck_sec", "int", "objective", "卡关时长：停滞、干等、无反馈的时间",
              unit="秒", rng=(0, 7200), note="折磨感的主要来源"),
    FieldSpec("stuck_ratio", "float", "objective", "卡关占比 = 卡关时长 / 耗时",
              rng=(0.0, 1.0), note="> 0.4 视为高风险"),

    # 进度与尝试
    FieldSpec("attempts", "int", "progress", "尝试次数：失败重开次数", rng=(1, 999)),
    FieldSpec("chest_open_count", "int", "progress", "本关开箱次数",
              layer="category", note="开箱品类专用"),
    FieldSpec("chest_interval_avg_sec", "float", "progress", "本关开箱平均间隔",
              unit="秒", layer="category"),
    FieldSpec("chest_interval_last_sec", "float", "progress", "本关最后一次开箱距结束的间隔",
              unit="秒", layer="category", note="间隔越来越长 = 厌倦的领先指标"),
    FieldSpec("resource_gap", "int", "progress", "卡住时还差多少资源",
              layer="category", note="判断是数值墙还是设计意图"),

    # 行为数字（录屏回看补，现场数不清）
    FieldSpec("actions_total", "int", "behavior", "总操作数"),
    FieldSpec("actions_valid", "int", "behavior", "有效操作数"),
    FieldSpec("actions_invalid", "int", "behavior", "无效操作数（点错、找不到按钮）"),
    FieldSpec("apm", "float", "behavior", "每分钟操作数 = 操作数 / 时长",
              note="骤降=卡住或挂机；骤升=烦躁乱点"),
    FieldSpec("longest_pause_sec", "float", "behavior", "最长单次停顿", unit="秒"),
    FieldSpec("pause_over5s_count", "int", "behavior", "停顿超过 5 秒的次数",
              note="干等的次数，比总卡关时长更敏感"),
    FieldSpec("skip_anim_count", "int", "behavior", "跳过动画 / 引导的次数"),
    FieldSpec("phone_check_count", "int", "behavior", "切屏 / 看手机次数",
              note="最强的厌倦信号，比任何自评都真"),
    FieldSpec("complaint_count", "int", "behavior", "口头抱怨次数"),
    FieldSpec("misclick_count", "int", "behavior", "误触次数"),
    FieldSpec("help_count", "int", "behavior", "求助次数（问策划 / 看提示）"),
    FieldSpec("used_ad_boost", "int", "behavior", "是否使用加速 / 广告（1=是）", rng=(0, 1)),

    # 主观感知
    FieldSpec("difficulty_delta", "int", "subjective", "难度增量：比上一关难多少，-2~+2",
              rng=(-2, 2), note="相对分而非绝对分，跨人可比，可累加成感知难度曲线"),
    FieldSpec("frustration_score", "int", "subjective", "挫败感 1-5", rng=(1, 5)),
    FieldSpec("challenge_score", "int", "subjective", "挑战感 1-5", rng=(1, 5),
              note="与挫败感是两个独立的分，不要合并"),
    FieldSpec("estimated_duration_sec", "int", "subjective", "玩家预估自己花了多久", unit="秒"),
    FieldSpec("time_dilation", "float", "subjective", "时间膨胀系数 = 预估 / 实际",
              rng=(0.1, 5.0), note="> 1.3 体感比实际长（枯燥/干等）；< 0.8 心流"),
    FieldSpec("perceived_drop_rate", "int", "subjective", "感知掉率：玩家估计的稀有物概率 (%)",
              layer="category", rng=(0, 100)),

    # 卡关归因
    FieldSpec("stuck_reason_code", "int", "attribution", "卡关原因编码 0-5 / 9", rng=(0, 9)),
    FieldSpec("stuck_choice_code", "int", "attribution", "卡关选择编码 0-4", rng=(0, 4)),

    # 结果代理指标（未上线阶段替代留存）
    FieldSpec("quit_at_level", "int", "outcome", "投射法：你认为普通新手会在第几关流失"),
    FieldSpec("next_day_return", "int", "outcome", "次日自发回归（1=回来了）", rng=(0, 1),
              note="小样本下最接近 D1 留存的指标"),

    # 元数据
    FieldSpec("note", "str", "meta", "备注原文（不参与统计，只用于解释异常）"),
    FieldSpec("is_synthetic", "int", "meta", "是否合成数据（1=仿真）", rng=(0, 1),
              note="合成数据必须在报告里显式标注，不得冒充真实数据"),
    FieldSpec("source_file", "str", "meta", "数据来源文件名"),
]

FIELD_BY_NAME: Dict[str, FieldSpec] = {f.name: f for f in FIELDS}
REQUIRED_FIELDS: List[str] = [f.name for f in FIELDS if f.required]


# 别名表：任何外部列名 → 规范字段名
#
# 别名表支持沿用原有表头。
# 新增一种表头，只需要在下面加一行，不用动任何计算代码。
ALIASES: Dict[str, str] = {
    # 中文采集表（templates/试玩采集表.xlsx 现场填写表）
    "玩家编号": "player_id",
    "玩家水平": "player_type",
    "游戏版本": "build_version",
    "版本号": "build_version",
    "日期": "test_date",
    "测试日期": "test_date",
    "关卡": "level_id",
    "关卡号": "level_id",
    "关卡ID": "level_id",
    "耗时": "duration_sec",
    "耗时秒": "duration_sec",
    "关卡耗时": "duration_sec",
    "主动时长": "active_sec",
    "主动推进时长": "active_sec",
    "卡关时长": "stuck_sec",
    "卡关占比": "stuck_ratio",
    "尝试次数": "attempts",
    "难度vs上一关": "difficulty_delta",
    "难度增量": "difficulty_delta",
    "挫败感": "frustration_score",
    "挑战感": "challenge_score",
    "预估耗时": "estimated_duration_sec",
    "预估耗时秒": "estimated_duration_sec",
    "时间膨胀系数": "time_dilation",
    "膨胀系数": "time_dilation",
    "卡关原因": "stuck_reason_code",
    "卡关选择": "stuck_choice_code",
    "备注": "note",
    "是否策划": "is_planning_staff",
    "次日回归": "next_day_return",
    "次日自发回归": "next_day_return",
    "流失关卡": "quit_at_level",

    # 英文常见变体（第三方平台 / 数据同学的表）
    "player": "player_id",
    "uid": "player_id",
    "userid": "player_id",
    "user_id": "player_id",
    "session": "session_id",
    "level": "level_id",
    "stage": "level_id",
    "stage_id": "level_id",
    "version": "build_version",
    "app_version": "build_version",
    "playtime": "duration_sec",
    "play_time": "duration_sec",
    "duration": "duration_sec",
    "cost_time": "duration_sec",
    "stuck_time": "stuck_sec",
    "try_count": "attempts",
    "retry": "attempts",
    "retry_count": "attempts",
    "chest_open": "chest_open_count",
    "open_chest_count": "chest_open_count",
    "frustration": "frustration_score",
    "challenge": "challenge_score",
    "est_duration": "estimated_duration_sec",
    "estimate": "estimated_duration_sec",
    "date": "test_date",
    "comment": "note",
    "remark": "note",
    "notes": "note",
}


STUCK_REASON_CODES: Dict[int, Dict[str, str]] = {
    0: {"label": "没卡住", "fix": "—"},
    1: {"label": "资源不够", "fix": "调产出 / 调消耗（数值问题）"},
    2: {"label": "概率不好（脸黑）", "fix": "加保底 / 降低方差（随机性问题）"},
    3: {"label": "没看懂规则", "fix": "改引导 / 改 UI 提示（信息问题）"},
    4: {"label": "操作繁琐", "fix": "减步骤 / 加一键（交互问题）"},
    5: {"label": "不知道要干嘛", "fix": "改目标呈现（动机问题）"},
    9: {"label": "其他（见备注）", "fix": "回看备注原文再定性"},
}

STUCK_CHOICE_CODES: Dict[int, Dict[str, str]] = {
    0: {"label": "没卡住", "meaning": "—"},
    1: {"label": "继续等 / 继续攒", "meaning": "容忍度高，或没有更好的出口"},
    2: {"label": "看广告", "meaning": "广告位自然触发"},
    3: {"label": "付费", "meaning": "付费意愿信号（内部测试只能问意愿，不能真付费）"},
    4: {"label": "想退出", "meaning": "最强流失信号，等同准流失点"},
}


def explain_field(field_name: str) -> str:
    """生成一个字段的口径说明文字，用于报告的「口径说明」章节。"""
    f = FIELD_BY_NAME.get(field_name)
    if not f:
        return field_name
    parts = [f.desc]
    if f.unit:
        parts.append("单位：" + f.unit)
    if f.rng:
        parts.append("合理区间：%s ~ %s" % (f.rng[0], f.rng[1]))
    if f.note:
        parts.append(f.note)
    return "；".join(parts)


def fields_of_layer(layer: str) -> List[FieldSpec]:
    """取某一层（universal / category / project）的全部字段。"""
    return [f for f in FIELDS if f.layer == layer]
