"""数据导入与规范化：把任意试玩表转成 Dataset。"""

from __future__ import annotations

import os
import re
import glob
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import schema


_BRACKET = re.compile(r"[（(][^）)]*[）)]")   # 括号及其内容，如 "耗时(秒)" -> "耗时"
_SPACE = re.compile(r"[\s\u3000]+")

# 归一化列名：去空白、去括号内容、横杠转下划线、全角斜杠转半角、小写
_norm = lambda name: _SPACE.sub(
    "", _BRACKET.sub("", str(name or ""))).replace("-", "_").replace("／", "/").strip().lower()

# 预构建归一化后的别名索引（避免每次映射重复计算）
_ALIAS_INDEX: Dict[str, str] = {}
for _k, _v in schema.ALIASES.items():
    _ALIAS_INDEX[_norm(_k)] = _v
for _f in schema.FIELDS:
    _ALIAS_INDEX[_norm(_f.name)] = _f.name
    _ALIAS_INDEX[_f.name.lower()] = _f.name


def map_raw_column(raw: str) -> Optional[str]:
    """把任意列名映射到规范字段名；映射不上返回 None。"""
    n = _norm(raw)
    if not n:
        return None
    if n in _ALIAS_INDEX:
        return _ALIAS_INDEX[n]
    # 二次尝试：去掉下划线后比对（duration_sec vs durationsec）
    n2 = n.replace("_", "")
    for k, v in _ALIAS_INDEX.items():
        if k.replace("_", "") == n2:
            return v
    return None


@dataclass
class Dataset:
    """规范化后的试玩数据。df 的列名即规范字段名，issues 记着导入时发现的问题，"""

    df: pd.DataFrame
    issues: List[dict] = field(default_factory=list)
    column_map: Dict[str, str] = field(default_factory=dict)
    unmapped: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    # 数据行数（样本量按 n_players 计）
    @property
    def n_rows(self) -> int:
        return len(self.df)

    @property
    def n_players(self) -> int:
        """玩家数。**注意：样本量的单位是玩家，不是行数**（见特色四）。"""
        if "player_id" not in self.df.columns:
            return 0
        return int(self.df["player_id"].nunique())

    # 出现过的关卡号，升序去重
    @property
    def levels(self) -> List[int]:
        if "level_id" not in self.df.columns:
            return []
        return sorted(int(x) for x in self.df["level_id"].dropna().unique())

    # 出现过的版本号，升序去重
    @property
    def versions(self) -> List[str]:
        if "build_version" not in self.df.columns:
            return []
        return sorted(str(x) for x in self.df["build_version"].dropna().unique())

    @property
    def is_synthetic(self) -> bool:
        """数据里是否含合成样本——决定报告要不要打「仿真数据」水印。"""
        if "is_synthetic" not in self.df.columns:
            return False
        return bool((pd.to_numeric(self.df["is_synthetic"], errors="coerce") == 1).any())

    def subset_by_version(self, version: str) -> "Dataset":
        """切出某个版本的子数据集。"""
        sub = self.df[self.df["build_version"].astype(str) == str(version)].copy()
        return Dataset(df=sub, issues=self.issues, column_map=self.column_map,
                       unmapped=self.unmapped, sources=self.sources)

    def has_fields(self, *cols: str) -> bool:
        """判断某几个字段是否都有有效值（用于决定能算哪些指标）。"""
        for c in cols:
            if c not in self.df.columns:
                return False
            if self.df[c].dropna().empty:
                return False
        return True

    def field_coverage(self) -> pd.DataFrame:
        """各规范字段的覆盖率，用于报告的「数据体检」章节。"""
        rows = []
        for f in schema.FIELDS:
            if f.name in self.df.columns:
                filled = int(self.df[f.name].notna().sum())
            else:
                filled = 0
            rows.append({
                "字段": f.name,
                "含义": f.desc,
                "层级": f.layer,
                "已填行数": filled,
                "覆盖率": (filled / self.n_rows) if self.n_rows else 0.0,
                "必填": "是" if f.required else "",
            })
        return pd.DataFrame(rows)


def read_file(path: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """读单个文件，返回 (原始 DataFrame, 元信息字典)。"""
    ext = os.path.splitext(path)[1].lower()
    meta: Dict[str, str] = {}

    if ext in (".xlsx", ".xlsm", ".xls"):
        try:
            from openpyxl import load_workbook

            wb = load_workbook(path, data_only=True)
            ws = next((wb[n] for n in wb.sheetnames if "填写表" in n), wb[wb.sheetnames[0]])
            grid = [[c for c in row] for row in ws.iter_rows(values_only=True)]

            # 表头行：前 15 行里，非空单元格 >= 3 且含「关卡」
            header_row = None
            for i, row in enumerate(grid[:15]):
                cells = [str(c) for c in row if c is not None and str(c).strip() != ""]
                if len(cells) >= 3 and any("关卡" in c for c in cells):
                    header_row = i
                    break
            if header_row is None:
                raise ValueError("没找到表头行（应包含「关卡」列）")
            headers = [str(c).strip() if c is not None else "" for c in grid[header_row]]

            # 表头之上的那对「标签行 + 值行」= 玩家级元信息
            for i in range(max(0, header_row - 1)):
                labels = [str(c).strip() if c is not None else "" for c in grid[i]]
                if not any(k in l for l in labels for k in ("玩家编号", "玩家水平", "游戏版本")):
                    continue
                vals = grid[i + 1] if i + 1 < len(grid) else []
                for j, lab in enumerate(labels):
                    if not lab:
                        continue
                    v = vals[j] if j < len(vals) else None
                    if v is None or str(v).strip() == "":
                        continue
                    meta[map_raw_column(lab) or lab] = str(v).strip()
                break

            # 数据行：表头之后，直到关卡列为空
            records = []
            for row in grid[header_row + 1:]:
                if not row or row[0] is None or str(row[0]).strip() == "":
                    break
                records.append({h: (row[j] if j < len(row) else None)
                                for j, h in enumerate(headers) if h})
            df = pd.DataFrame(records)
        except Exception:
            df = pd.read_excel(path)
    else:
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                df = pd.read_csv(path, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "无法解码文件：%s" % path)

    # 去掉完全没名字的列（Excel 常见的 Unnamed: x）
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    return df, meta


def to_fact_table(df: pd.DataFrame, meta: Optional[Dict[str, str]] = None,
              source: str = "") -> Dataset:
    """把原始 DataFrame 规范化成 PlayPulse 事实表。"""
    meta = meta or {}
    issues: List[dict] = []
    column_map: Dict[str, str] = {}
    unmapped: List[str] = []

    out = pd.DataFrame(index=df.index)

    # 1. 列名映射
    for raw in df.columns:
        std = map_raw_column(raw)
        if std is None:
            unmapped.append(str(raw))
            # 未知列原样保留，不静默丢弃
            out["__extra__" + str(raw)] = df[raw]
            continue
        column_map[str(raw)] = std
        if std in out.columns and std not in ("note",):
            # 同一规范字段被多列映射：保留非空更多的那一列
            if out[std].notna().sum() >= df[raw].notna().sum():
                continue
        out[std] = df[raw]

    if unmapped:
        issues.append({"level": "info", "code": "unmapped_columns",
                       "msg": "以下列没能映射到规范字段，已原样保留（不影响已识别字段的分析）："
                              + "、".join(unmapped[:10])})

    # 2. 元信息回填（现场填写表：玩家信息只填了一次）
    for k, v in meta.items():
        if k in out.columns:
            if out[k].notna().any() and str(out[k].dropna().iloc[0]).strip() != "":
                continue
        out[k] = v

    # 3. 类型强转
    for f in schema.FIELDS:
        if f.name not in out.columns:
            continue
        s = out[f.name]
        if f.dtype in ("int", "float"):
            num = pd.to_numeric(s, errors="coerce")
            bad = s.notna() & num.isna()
            if bad.any():
                issues.append({"level": "warn", "code": "type_error",
                               "msg": "字段 %s 有 %d 个非数字值，已置空" % (f.name, int(bad.sum()))})
            if f.dtype == "int":
                num = num.round()
            out[f.name] = num
        else:
            out[f.name] = s.astype("string").replace({"nan": None, "": None})

    # 4. 兜底字段（缺了就用文件名/版本号补，保证核心指标能算）
    if "player_id" not in out.columns or out["player_id"].isna().all():
        fallback = os.path.splitext(os.path.basename(source))[0] if source else "P01"
        out["player_id"] = fallback
        issues.append({"level": "warn", "code": "missing_player_id",
                       "msg": "缺少玩家编号，已用文件名兜底：%s（样本量统计会不准）" % fallback})

    if "build_version" not in out.columns or out["build_version"].isna().all():
        out["build_version"] = "v0"
        issues.append({"level": "warn", "code": "missing_version",
                       "msg": "缺少游戏版本，已填 v0。**版本对比 Diff 需要版本号，调参后务必换号**"})

    if "level_id" not in out.columns:
        issues.append({"level": "error", "code": "missing_level_id",
                       "msg": "缺少关卡列，无法做任何关卡维度分析"})
        raise ValueError("缺少关卡列（level_id），无法规范化：%s" % source)

    if "session_id" not in out.columns or out["session_id"].isna().all():
        out["session_id"] = (out["player_id"].astype(str) + "@" +
                             out["build_version"].astype(str))

    # 5. 派生字段（能算的都补上，减少采集负担）
    dur = pd.to_numeric(out.get("duration_sec"), errors="coerce")
    stuck = pd.to_numeric(out.get("stuck_sec"), errors="coerce")

    if "stuck_ratio" not in out.columns or out["stuck_ratio"].isna().all():
        if dur is not None and stuck is not None:
            out["stuck_ratio"] = (stuck / dur).where(dur > 0)

    if ("active_sec" not in out.columns) or out["active_sec"].isna().all():
        if dur is not None and stuck is not None:
            out["active_sec"] = (dur - stuck).clip(lower=0)

    est = pd.to_numeric(out.get("estimated_duration_sec"), errors="coerce")
    if ("time_dilation" not in out.columns) or out["time_dilation"].isna().all():
        if est is not None and dur is not None:
            out["time_dilation"] = (est / dur).where(dur > 0)

    # 6. 数值区间体检
    for f in schema.FIELDS:
        if f.rng is None or f.name not in out.columns:
            continue
        if f.dtype not in ("int", "float"):
            continue
        s = pd.to_numeric(out[f.name], errors="coerce")
        lo, hi = f.rng
        bad = s.notna() & ((s < lo) | (s > hi))
        if bad.any():
            issues.append({"level": "warn", "code": "out_of_range",
                           "msg": "字段 %s 有 %d 个值超出合理区间 [%s, %s]，请核对"
                                  % (f.name, int(bad.sum()), lo, hi)})

    # 7. 清理：耗时和卡关时长都为空的行没有信息量，丢掉
    before = len(out)
    if dur is not None:
        out = out[dur.notna() | out.get("stuck_sec", pd.Series(dtype=float)).notna()]
    dropped = before - len(out)
    if dropped:
        issues.append({"level": "info", "code": "dropped_empty_rows",
                       "msg": "丢弃 %d 行（耗时与卡关时长都为空）" % dropped})

    if "source_file" not in out.columns or out["source_file"].isna().all():
        out["source_file"] = os.path.basename(source) if source else ""

    out = out.reset_index(drop=True)
    return Dataset(df=out, issues=issues, column_map=column_map,
                   unmapped=unmapped, sources=[source] if source else [])


def load_auto(path: str) -> Dataset:
    """唯一入口：路径是文件就读文件，是目录就读目录下所有表格。"""
    if os.path.isdir(path):
        paths = sorted(glob.glob(os.path.join(path, "*.xlsx"))) + \
            sorted(glob.glob(os.path.join(path, "*.csv")))
    else:
        paths = [path]

    parts: List[pd.DataFrame] = []
    issues: List[dict] = []
    column_map: Dict[str, str] = {}
    unmapped: List[str] = []
    sources: List[str] = []

    for p in paths:
        try:
            df, meta = read_file(p)
            ds = to_fact_table(df, meta, source=p)
        except Exception as e:
            issues.append({"level": "error", "code": "load_failed",
                           "msg": "读取失败：%s —— %s" % (p, e)})
            continue
        parts.append(ds.df)
        issues.extend(ds.issues)
        column_map.update(ds.column_map)
        unmapped.extend(ds.unmapped)
        sources.extend(ds.sources)

    if not parts:
        raise ValueError("没有任何文件被成功读取")

    merged = pd.concat(parts, ignore_index=True, sort=False)

    key = ["player_id", "build_version", "level_id"]
    if all(k in merged.columns for k in key):
        dup = merged.duplicated(subset=key, keep="last")
        if dup.any():
            issues.append({"level": "info", "code": "duplicates",
                           "msg": "发现 %d 条重复记录（同一玩家+版本+关卡），已保留最后一条"
                                  % int(dup.sum())})
            merged = merged[~dup].reset_index(drop=True)

    # 多文件合并时去重排序，单文件保留表中原有的列顺序
    return Dataset(df=merged, issues=issues, column_map=column_map,
                   unmapped=sorted(set(unmapped)) if len(parts) > 1 else unmapped,
                   sources=sources)
