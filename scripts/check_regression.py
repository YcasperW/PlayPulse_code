"""全流程对拍：给定项目根目录，跑一遍完整流程并输出摘要，用于新旧版本对比。

用法：python scripts/check_regression.py <root> <data_path>
"""
import hashlib
import json
import re
import sys

import pandas as pd

ROOT = sys.argv[1]
DATA = sys.argv[2]
sys.path.insert(0, ROOT)

from playpulse.semantics import expr as ex          # noqa: E402
from playpulse.semantics.registry import default_ontology   # noqa: E402
from playpulse.semantics.compute import compute_level_metrics, compute_composites  # noqa: E402
from playpulse.semantics.compute import split_quadrants, player_level_matrix  # noqa: E402
from playpulse.insights import run_pulse_check      # noqa: E402
from playpulse.io_load import load_auto             # noqa: E402
from playpulse.report import build_report           # noqa: E402


def sig(obj):
    """把任意对象压成可比较的字符串。"""
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: sig(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sig(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return obj.round(6).to_dict(orient="list")
    return obj


out = {}

# 1. 表达式引擎
out["expr"] = {
    "safe_div": sig(list(ex.safe_eval("safe_div(stuck_sec, duration_sec)", {
        "stuck_sec": pd.Series([1.0, 2.0, 3.0]),
        "duration_sec": pd.Series([4.0, 0.0, 6.0])}))),
    "max_ratio": sig(list(ex.safe_eval("max_ratio(x)", {"x": pd.Series([10.0, 20.0, 40.0])}))),
    "zscore_flat": sig(list(ex.safe_eval("zscore(x)", {"x": pd.Series([1.0, 1.0, 1.0])}))),
    "clip": sig(list(ex.safe_eval("clip(x, 0, 1)", {"x": pd.Series([-1.0, 0.5, 2.0])}))),
    "scalar": sig(ex.safe_eval("safe_div(a, b)", {"a": 3.0, "b": 0.0})),
}

# 2. 指标本体
onto = default_ontology()
out["onto"] = {"metrics": len(onto.metrics), "composites": len(onto.composites),
               "rules": len(onto.rules), "thresholds": sig(onto.thresholds)}

# 3. 数据 + 计算 + 诊断
ds = load_auto(DATA)
out["data"] = {"rows": ds.n_rows, "players": ds.n_players,
               "levels": ds.levels, "versions": ds.versions}

lm = compute_level_metrics(ds, onto)
compute_composites(lm, onto)
out["metrics"] = sig(lm.long[["metric", "level_id", "point", "ci_low", "ci_high", "n", "verdict"]]
                     .round(6).to_dict(orient="list"))
out["skipped"] = sig(lm.skipped)

pc = run_pulse_check(ds, onto)
out["findings"] = [(f.name, f.severity, sig(f.value), f.conclusion[:80]) for f in pc.findings]
out["quadrants"] = sig(split_quadrants(lm, onto).to_dict(orient="list"))
out["matrix_shape"] = list(player_level_matrix(ds, onto, "duration_sec").shape)

# 4. 报告。生成时间每次都不同，先抹掉再做 md5，否则对拍永远对不上
rep = build_report(pc, with_charts=False)
stable = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", "<TS>", rep.markdown)
out["report"] = {"len": len(rep.markdown), "html_len": len(rep.html),
                 "sha": hashlib.md5(stable.encode("utf-8")).hexdigest()}

print(json.dumps(out, ensure_ascii=False, default=str))
