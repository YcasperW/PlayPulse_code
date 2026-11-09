"""指标本体：加载并校验 metrics.yaml，提供口径查询与导出。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

import yaml

from . import expr
from .. import schema


DEFAULT_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics.yaml")


@dataclass
class MetricDef:
    """一个基础指标的定义。"""
    id: str
    name: str
    layer: str
    unit: str = ""
    formula: str = ""
    agg: str = "median"
    min_n: int = 5
    direction: str = "neutral"       # higher_is_worse / higher_is_better / neutral
    warn_above: Optional[float] = None
    warn_below: Optional[float] = None
    why: str = ""
    fix: str = ""
    caveat: str = ""
    applies_to: List[str] = field(default_factory=list)

    def required_fields(self) -> List[str]:
        """公式里引用了哪些字段（用于判断这批数据能不能算这个指标）。"""
        return _fields_in(self.formula)


@dataclass
class ComponentDef:
    metric: str
    weight: float
    reason: str = ""


@dataclass
class CompositeDef:
    """复合指标：由多个基础指标加权合成。"""
    id: str
    name: str
    layer: str
    unit: str = ""
    norm: str = "max_ratio"          # max_ratio / cumulative
    min_n: int = 5
    direction: str = "neutral"
    why: str = ""
    components: List[ComponentDef] = field(default_factory=list)


@dataclass
class RuleDef:
    """一条洞察规则：什么算异常。"""
    id: str
    name: str
    layer: str
    on: str
    kind: str                        # jump / threshold / rank_gap
    threshold: float
    min_n: int = 5
    severity: str = "medium"         # high / medium / low
    why: str = ""
    fix: str = ""
    against: str = ""                # rank_gap 用的对照指标
    quadrants: Dict[str, str] = field(default_factory=dict)
    target_shape: str = ""


# 从公式里抽出引用的规范字段名（函数名排除在外）
_fields_in = lambda f: sorted(
    i for i in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", f or ""))
    if i in schema.FIELD_BY_NAME and i not in expr.FUNCTIONS)


class Ontology:
    """指标本体：metrics.yaml 的内存表示。"""

    # 把 metrics.yaml 解析后的原始 dict 构成本体对象
    def __init__(self, raw: dict):
        self.raw = raw
        self.version = raw.get("version")
        self.updated = raw.get("updated")
        self.domain = raw.get("domain")
        self.thresholds: Dict[str, Any] = raw.get("thresholds", {}) or {}

        self.metrics: Dict[str, MetricDef] = {}
        for m in raw.get("metrics", []) or []:
            md = MetricDef(
                id=m["id"], name=m.get("name", m["id"]), layer=m.get("layer", "universal"),
                unit=m.get("unit", ""), formula=m.get("formula", ""),
                agg=m.get("agg", "median"), min_n=int(m.get("min_n", 5)),
                direction=m.get("direction", "neutral"),
                warn_above=m.get("warn_above"), warn_below=m.get("warn_below"),
                why=_clean(m.get("why", "")), fix=_clean(m.get("fix", "")),
                caveat=_clean(m.get("caveat", "")),
                applies_to=m.get("applies_to", []) or [],
            )
            self.metrics[md.id] = md

        self.composites: Dict[str, CompositeDef] = {}
        for c in raw.get("composites", []) or []:
            cd = CompositeDef(
                id=c["id"], name=c.get("name", c["id"]), layer=c.get("layer", "universal"),
                unit=c.get("unit", ""), norm=c.get("norm", "max_ratio"),
                min_n=int(c.get("min_n", 5)), direction=c.get("direction", "neutral"),
                why=_clean(c.get("why", "")),
                components=[ComponentDef(metric=x["metric"], weight=float(x.get("weight", 1.0)),
                                         reason=_clean(x.get("reason", "")))
                            for x in c.get("components", []) or []],
            )
            self.composites[cd.id] = cd

        self.rules: Dict[str, RuleDef] = {}
        for r in raw.get("rules", []) or []:
            # 注意：YAML 1.1 会把裸键 `on:` 解析成布尔 True（on/off 是布尔别名），
            # 所以配置文件里改用 `target:`，这里同时兼容两种写法。
            target = r.get("target") or r.get("on") or r.get(True)
            if not target:
                raise ValueError("规则 %s 缺少 target（作用指标）字段" % r.get("id"))
            rd = RuleDef(
                id=r["id"], name=r.get("name", r["id"]), layer=r.get("layer", "universal"),
                on=str(target), kind=r.get("kind", "threshold"),
                threshold=float(r.get("threshold", 1.0)),
                min_n=int(r.get("min_n", 5)), severity=r.get("severity", "medium"),
                why=_clean(r.get("why", "")), fix=_clean(r.get("fix", "")),
                against=r.get("against", ""),
                quadrants=r.get("quadrants", {}) or {},
                target_shape=_clean(r.get("target_shape", "")),
            )
            self.rules[rd.id] = rd

    # 读 YAML 构成本体，默认顺带校验全部公式
    @classmethod
    def load_from_yaml(cls, path: str = DEFAULT_YAML, validate: bool = True) -> "Ontology":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        onto = cls(raw)
        if validate:
            onto.validate_formulas()
        return onto

    def validate_formulas(self) -> List[str]:
        """校验所有公式能被安全求值器接受。返回警告列表（空 = 全部通过）。"""
        warnings: List[str] = []
        vars_stub = {f.name: 1.0 for f in schema.FIELDS}
        vars_stub.update({k: 1.0 for k in expr.FUNCTIONS})

        for mid, m in self.metrics.items():
            if not m.formula:
                warnings.append("指标 %s 没有公式" % mid)
                continue
            try:
                expr.safe_eval(m.formula, vars_stub)
            except expr.ExprError as e:
                raise ValueError("metrics.yaml 中指标 [%s] 的公式有问题：%s" % (mid, e))
            # 公式里引用的字段必须是已知字段
            unknown = [x for x in _fields_in(m.formula) if x not in schema.FIELD_BY_NAME]
            if unknown:
                warnings.append("指标 %s 引用了未知字段：%s" % (mid, unknown))

        for cid, c in self.composites.items():
            if not c.components:
                raise ValueError("复合指标 %s 没有 components" % cid)
            for comp in c.components:
                if comp.metric not in self.metrics:
                    raise ValueError(
                        "复合指标 %s 引用了不存在的指标：%s" % (cid, comp.metric))
            total = sum(x.weight for x in c.components)
            if abs(total - 1.0) > 0.011 and c.norm != "cumulative":
                warnings.append("复合指标 %s 的权重和为 %.2f（建议归一到 1.0）" % (cid, total))
        return warnings

    # 按 id 取指标定义
    def metric(self, metric_id: str) -> Optional[MetricDef]:
        return self.metrics.get(metric_id)

    # 取某一层（通用 / 品类 / 项目）的全部指标
    def metrics_of_layer(self, layer: str) -> List[MetricDef]:
        return [m for m in self.metrics.values() if m.layer == layer]

    def computable_ids(self, dataset) -> List[MetricDef]:
        """这批数据能算哪些指标（依据字段是否存在且有值）。"""
        out = []
        for m in self.metrics.values():
            if all(dataset.has_fields(f) for f in m.required_fields()):
                out.append(m)
        return out

    def blocked_with_reason(self, dataset) -> List[MetricDef]:
        """算不了的指标 + 缺什么字段。报告里要明确告诉用户，而不是默默跳过。"""
        out = []
        for m in self.metrics.values():
            missing = [f for f in m.required_fields() if not dataset.has_fields(f)]
            if missing:
                out.append((m, missing))
        return out

    # 导出：口径文档
    def to_markdown(self) -> str:
        """把口径导出成 Markdown，可直接贴进需求文档 / Wiki。"""
        lines = [
            "# PlayPulse 指标口径说明",
            "",
            "> 这份文档由 `playpulse/semantics/metrics.yaml` 自动生成，",
            "> 是本项目指标口径的**唯一事实来源**。",
            "> 策划与数据分析以本文档为准，口径变更需同步更新 YAML 并走 review。",
            "",
            "- 本体版本：%s" % self.version,
            "- 更新日期：%s" % self.updated,
            "- 适用域：%s" % self.domain,
            "",
        ]

        for layer, title in (("universal", "通用层（强制统一口径，改动影响跨项目基准线）"),
                             ("category", "品类层（可插拔）"),
                             ("project", "项目层（项目自定义）")):
            ms = self.metrics_of_layer(layer)
            if not ms:
                continue
            lines += ["## %s" % title, ""]
            lines += ["| 指标 | 口径 | 单位 | 聚合 | 最低样本量 | 方向 |",
                      "|---|---|---|---|---|---|"]
            for m in ms:
                direction = {"higher_is_worse": "越高越差", "higher_is_better": "越高越好",
                             "neutral": "中性"}.get(m.direction, m.direction)
                lines.append("| %s | `%s` | %s | %s | %d 人 | %s |"
                             % (m.name, m.formula, m.unit or "-", m.agg, m.min_n, direction))
            lines.append("")

            for m in ms:
                if not (m.why or m.caveat or m.fix):
                    continue
                lines.append("### %s" % m.name)
                if m.why:
                    lines.append("")
                    lines.append("**为什么看它**：%s" % m.why)
                if m.caveat:
                    lines.append("")
                    lines.append("**注意**：%s" % m.caveat)
                if m.fix:
                    lines.append("")
                    lines.append("**怎么改**：%s" % m.fix)
                lines.append("")

        if self.composites:
            lines += ["## 复合指标（人话 → 计算的映射）", ""]
            for c in self.composites.values():
                lines.append("### %s" % c.name)
                lines.append("")
                if c.why:
                    lines.append(c.why)
                    lines.append("")
                lines += ["| 组成指标 | 权重 | 理由 |", "|---|---|---|"]
                for comp in c.components:
                    src = self.metrics.get(comp.metric)
                    lines.append("| %s | %.0f%% | %s |"
                                 % (src.name if src else comp.metric,
                                    comp.weight * 100, comp.reason))
                lines.append("")

        if self.rules:
            lines += ["## 异常判定规则", ""]
            lines += ["| 规则 | 判据 | 阈值 | 严重度 | 修法 |", "|---|---|---|---|---|"]
            for r in self.rules.values():
                judge = {"jump": "相邻关卡跳变比", "threshold": "阈值",
                         "rank_gap": "排名背离"}.get(r.kind, r.kind)
                lines.append("| %s | %s（%s） | %s | %s | %s |"
                             % (r.name, judge, r.on, r.threshold,
                                {"high": "高", "medium": "中", "low": "低"}.get(r.severity, r.severity),
                                (r.fix or "—").replace("\n", " ")))
            lines.append("")

        return "\n".join(lines)


# YAML 里用 > 折叠的多行文本会带换行，压成单行
_clean = lambda s: " ".join(str(s).split()) if s else ""


# 全局默认本体（首次访问时加载，避免每次调用都读文件）
_ONTO: Optional[Ontology] = None


# 全局单例本体，避免每次都重新解析 YAML
def default_ontology(path: str = DEFAULT_YAML) -> Ontology:
    global _ONTO
    if _ONTO is None:
        _ONTO = Ontology.load_from_yaml(path)
    return _ONTO
