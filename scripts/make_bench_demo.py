"""生成 PlayBench 演示用的合成项目数据（is_synthetic=1）。"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "data", "raw", "试玩数据-合成-100份.csv")
OUT = os.path.join(ROOT, "data", "projects")

# 项目名 → 每关的耗时倍率（相对第 1 关）。曲线的陡度就是这几个数的差别
PROJECTS = {
    "星屿开箱": [1.00, 1.22, 1.48],
    "甜品消消乐": [1.00, 1.45, 2.05],
    "机械迷城": [1.00, 1.12, 1.30],
}

N_PLAYERS = 60


def main() -> int:
    # 原始表是中文表头，先走一遍规范化，拿到标准字段名再派生
    from playpulse.io_load import load_auto

    base = load_auto(SRC).df
    rng = np.random.default_rng(20260924)

    keep = sorted(base["player_id"].astype(str).unique())[:N_PLAYERS]
    base = base[base["player_id"].astype(str).isin(keep)].copy()

    for name, scales in PROJECTS.items():
        df = base.copy()
        lv = df["level_id"].astype(int)
        mult = np.array([scales[int(x) - 1] for x in lv])

        noise = rng.normal(1.0, 0.08, len(df)).clip(0.75, 1.35)
        df["duration_sec"] = (df["duration_sec"] * mult * noise).round().astype(int).clip(5)

        # 卡关随难度一起涨，但涨得更快一点：陡的关卡不只是长，是更磨人
        stuck_mult = mult ** 1.4 * rng.normal(1.0, 0.10, len(df)).clip(0.7, 1.4)
        stuck = (df["stuck_sec"].fillna(0) * stuck_mult).round().astype(int)
        df["stuck_sec"] = np.minimum(stuck, (df["duration_sec"] * 0.85).astype(int))
        df["stuck_ratio"] = (df["stuck_sec"] / df["duration_sec"]).round(4)

        df["attempts"] = (df["attempts"] * (mult ** 0.8)
                          * rng.normal(1.0, 0.12, len(df)).clip(0.8, 1.3)).round().astype(int).clip(1)

        df["player_id"] = name[:2] + "_" + df["player_id"].astype(str)
        df["build_version"] = "V1"
        df["is_synthetic"] = 1

        d = os.path.join(OUT, name)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "试玩数据.csv")
        df.to_csv(p, index=False, encoding="utf-8-sig")
        pivot = df.groupby("level_id")["duration_sec"].median()
        print("%-10s %d 人 × %d 关 → %s" % (name, df["player_id"].nunique(),
                                            df["level_id"].nunique(), p))
        print("            关卡耗时中位数：" +
              " → ".join("%.0f 秒" % v for v in pivot.values))

    print("\n接下去：")
    print("  python -m playpulse bench add data/projects/星屿开箱/试玩数据.csv --project 星屿开箱")
    return 0


if __name__ == "__main__":
    sys.exit(main())
