"""命令行入口：check / diff / ask / glossary / bench。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional


# 打印导入期的问题清单和未能识别的列
def _print_issues(ds, verbose: bool = True) -> None:
    if not verbose or not getattr(ds, "issues", None):
        return
    print("\n[数据体检]")
    for it in ds.issues:
        tag = {"error": "错误", "warn": "警告", "info": "提示"}.get(it.get("level"), "提示")
        print("  [%s] %s" % (tag, it.get("msg", "")))
    if getattr(ds, "unmapped", None):
        print("  未识别的列（不影响分析，但建议你确认）：%s" % "、".join(map(str, ds.unmapped)))


# 四个子命令都要「读数据 → 跑体检」，抽出来省得复制四遍
def _load_and_check(path: str, quiet: bool = False):
    from .io_load import load_auto
    from .insights import run_pulse_check

    ds = load_auto(path)
    _print_issues(ds, not quiet)
    return ds, run_pulse_check(ds)


# check 子命令：读数据 → 跑体检 → 落盘报告
def cmd_check(args) -> int:
    from .report import build_report

    print("读取：%s" % args.path)
    ds, pc = _load_and_check(args.path, args.quiet)
    print("  %d 行 / %d 位玩家 / %d 关 / 版本 %s"
          % (ds.n_rows, ds.n_players, len(ds.levels), "、".join(map(str, ds.versions))))

    diff = None
    if args.compare:
        try:
            from .diff import version_diff
            a, b = args.compare.split(",")
            diff = version_diff(ds, pc.ontology, a.strip(), b.strip())
        except Exception as e:
            print("  版本对比跳过：%s" % e)

    # 给了 --project 就顺便存进基准库；对比先于存档，避免把当前项目算进基准
    bench = None
    if args.project:
        from .bench import compare_to_baseline, save_run
        bench = compare_to_baseline(pc, project=args.project)
        print("已存入基准库：%s" % save_run(pc, args.project, note=args.note or ""))
        print("  " + bench.verdict_sentence())

    r = build_report(pc, diff=diff, bench=bench, with_charts=not args.no_charts)
    paths = r.save_files(args.out, args.name, excel=not args.no_excel)
    print("\n报告已生成：")
    for k, v in paths.items():
        print("  %s → %s" % (k, v))

    print("\n" + "=" * 60)
    print(r.meta.get("headline", "一句话结论：见报告").replace("**", ""))
    print("=" * 60)
    return 0


# diff 子命令：两个版本按关卡逐项对比
def cmd_diff(args) -> int:
    from .diff import version_diff
    from .report import build_report

    ds, pc = _load_and_check(args.path, args.quiet)
    try:
        vd = version_diff(ds, pc.ontology, args.a, args.b)
    except ValueError as e:
        print("错误：%s" % e)
        print("  数据里可用的版本：%s" % ds.versions)
        return 2

    print("\n%s → %s" % (vd.ver_a, vd.ver_b))
    print("  基线 %d 人 / 对照 %d 人" % (vd.n_a, vd.n_b))
    print("\n" + vd.verdict_sentence())
    for w in vd.warnings:
        print("  ※ %s" % w)
    if len(vd.conclusions) > 1:
        print("\n分关卡结论：")
        for c in vd.conclusions[1:]:
            print("  - %s" % c)

    if args.export:
        r = build_report(pc, diff=vd, title="版本对比报告 %s → %s" % (vd.ver_a, vd.ver_b))
        paths = r.save_files(args.out, args.name, excel=not args.no_excel)
        print("\n已导出：%s" % paths["html"])
        if "excel" in paths:
            print("        %s" % paths["excel"])
    return 0


# ask 子命令：把一句提问路由到预定义分析卡片
def cmd_ask(args) -> int:
    from .cards import ask, CARDS

    if args.list:
        print("当前可用的分析卡片：")
        for c in CARDS:
            print("  %-18s %s —— %s" % (c.id, c.question, c.why))
        return 0

    ds, pc = _load_and_check(args.path, args.quiet)
    res = ask(pc, args.question)
    print("\n" + res.render_text())
    return 0 if res.matched else 1


# 只打了 bench 没带子命令时，打印 bench 的用法
def cmd_bench_help(args) -> int:
    from .bench import MIN_PROJECTS, bench_root
    print("PlayBench 跨项目基准线（基准库存放在 %s）" % bench_root())
    print("  add      把一次体检结果存进某个项目")
    print("  list     列出已入库的项目")
    print("  show     打印基准线（各指标各关卡的分位分布）")
    print("  compare  本次体检 vs 基准线")
    print("\n基准线的样本量单位是项目数，不足 %d 个就不给基准线。" % MIN_PROJECTS)
    return 0


# bench add：跑一次体检后把结果存进这个项目自己的目录
def cmd_bench_add(args) -> int:
    from .bench import save_run

    ds, pc = _load_and_check(args.path, args.quiet)
    path = save_run(pc, args.project, note=args.note or "")
    print("已存入基准库：%s" % path)
    print("  %s / %d 人 / %d 关 / 版本 %s"
          % (args.project, pc.n_players, len(ds.levels), "、".join(map(str, ds.versions))))
    print("  存的是通用层指标的点估计，原始行不落库（项目之间只共享数字，不共享数据）。")
    return 0


# bench list：列出基准库里有哪些项目
def cmd_bench_list(args) -> int:
    from .bench import list_projects, MIN_PROJECTS

    t = list_projects()
    if t.empty:
        print("基准库还是空的。先跑：playpulse bench add <数据> --project 项目名")
        return 0
    print(t.to_string(index=False))
    print("\n%d 个项目已入库，基准线门槛是 %d 个。" % (len(t), MIN_PROJECTS))
    return 0


# bench show：打印基准线本身（各指标各关卡的分位分布）
def cmd_bench_show(args) -> int:
    from .bench import (build_baseline, baseline_table_for_display,
                        MIN_PROJECTS, DEFAULT_METRICS)
    from .semantics import default_ontology

    b = build_baseline(exclude=args.exclude, min_projects=args.min_projects,
                       metrics=None if args.all else list(DEFAULT_METRICS))
    if b.table.empty:
        print("还没有可算基准线的记录。")
        return 0
    t = baseline_table_for_display(b, default_ontology())
    cols = ["指标", "关卡", "n_projects", "p25", "p50", "p75", "p90", "基准可信"]
    print(t[cols].to_string(index=False))
    print("\n项目数 %d（含 %s）｜门槛 %d｜不够 3 个项目的行标注为不可信，不要拿去当基准。"
          % (b.n_projects, "、".join(b.projects[:6]), MIN_PROJECTS))
    return 0


# bench compare：本次体检 vs 基准线
def cmd_bench_compare(args) -> int:
    from .bench import compare_to_baseline

    ds, pc = _load_and_check(args.path, args.quiet)
    cmp = compare_to_baseline(pc, project=args.project, min_projects=args.min_projects)

    print("\n" + "=" * 60)
    print(cmp.verdict_sentence())
    print("=" * 60)
    if cmp.table.empty:
        print("没有可对上的通用层指标。")
        return 0
    show = cmp.table[["指标", "关卡", "本次", "基准中位数", "P25", "P75", "差%", "位置", "项目数"]]
    print(show.to_string(index=False))
    for w in cmp.warnings:
        print("  ※ %s" % w)
    return 0


def cmd_glossary(args) -> int:
    """导出口径文档。"""
    from .semantics import default_ontology
    onto = default_ontology()
    md = onto.to_markdown()
    out = args.out or "口径说明.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print("口径文档已导出：%s（%d 个指标 / %d 条规则）"
          % (out, len(onto.metrics), len(onto.rules)))
    bad = onto.validate_formulas()
    if bad:
        print("注意：以下指标公式校验未通过：")
        for b in bad:
            print("  - %s" % b)
    return 0


# 构建 argparse 解析器及四个子命令的参数
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="playpulse",
        description="PlayPulse · 把脉 —— 游戏难度体检。默认动作是直接出报告，不用你提问。")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="上传数据 → 直接出难度体检报告")
    c.add_argument("path", help="CSV / Excel 文件，或一整个目录")
    c.add_argument("-o", "--out", default="out", help="输出目录，默认 out/")
    c.add_argument("-n", "--name", default="难度体检报告", help="报告文件名")
    c.add_argument("--compare", default=None, help="同时做版本对比，格式：V1,V2")
    c.add_argument("--project", default=None,
                   help="归入哪个项目：存进基准库，并在报告里加一节「与基准线的对比」")
    c.add_argument("--note", default="", help="这次测试的备注，会写进基准库记录")
    c.add_argument("--no-charts", action="store_true", help="不生成图表（更快）")
    c.add_argument("--no-excel", action="store_true", help="不生成 Excel 报告（默认会生成）")
    c.add_argument("-q", "--quiet", action="store_true")
    c.set_defaults(func=cmd_check)

    d = sub.add_parser("diff", help="两个版本对比")
    d.add_argument("path")
    d.add_argument("a", help="基线版本（旧）")
    d.add_argument("b", help="对照版本（新）")
    d.add_argument("-o", "--out", default="out")
    d.add_argument("-n", "--name", default="版本对比报告")
    d.add_argument("--export", action="store_true", help="同时导出 HTML + Excel 报告")
    d.add_argument("--no-excel", action="store_true", help="不生成 Excel 报告（默认会生成）")
    d.add_argument("-q", "--quiet", action="store_true")
    d.set_defaults(func=cmd_diff)

    a = sub.add_parser("ask", help="问一句人话（路由到预定义分析卡片，不生成 SQL）")
    a.add_argument("path")
    a.add_argument("question", nargs="?", default="")
    a.add_argument("--list", action="store_true", help="列出所有可用卡片")
    a.add_argument("-q", "--quiet", action="store_true")
    a.set_defaults(func=cmd_ask)

    g = sub.add_parser("glossary", help="导出口径文档（给业务和数据对齐）")
    g.add_argument("-o", "--out", default="口径说明.md")
    g.set_defaults(func=cmd_glossary)

    b = sub.add_parser("bench", help="PlayBench 跨项目基准线（存 / 看 / 比）")
    b.set_defaults(func=cmd_bench_help)
    bs = b.add_subparsers(dest="bench_cmd")

    ba = bs.add_parser("add", help="把一次体检结果存进某个项目")
    ba.add_argument("path")
    ba.add_argument("--project", required=True, help="项目名。一个项目一个目录，目录即隔离边界")
    ba.add_argument("--note", default="", help="备注，比如「第 3 轮内部试玩」")
    ba.add_argument("-q", "--quiet", action="store_true")
    ba.set_defaults(func=cmd_bench_add)

    bl = bs.add_parser("list", help="列出基准库里的项目")
    bl.set_defaults(func=cmd_bench_list)

    bsh = bs.add_parser("show", help="打印基准线本身")
    bsh.add_argument("--exclude", default=None, help="算基准线时排除某个项目（比自己时要用）")
    bsh.add_argument("--all", action="store_true", help="显示全部通用层指标（默认只显示 7 个核心指标）")
    bsh.add_argument("--min-projects", type=int, default=3)
    bsh.set_defaults(func=cmd_bench_show)

    bc = bs.add_parser("compare", help="本次体检 vs 基准线")
    bc.add_argument("path")
    bc.add_argument("--project", default=None, help="当前项目名，算基准时会把自己排除掉")
    bc.add_argument("--min-projects", type=int, default=3)
    bc.add_argument("-q", "--quiet", action="store_true")
    bc.set_defaults(func=cmd_bench_compare)

    return p


# CLI 入口，按子命令分发
def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
