#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口：跑完整个数据分析流程。

用法：
    python main.py                          # 自动探测数据（Boss 抓取产物优先 → 演示数据兜底）
    python main.py --data data/raw/xxx.csv  # 指定数据文件（问卷 CSV 必须这样显式指定）
    python main.py --no-charts              # 只算数、不出图
    python main.py --top 8                  # 专业维度取前 8 名

输出：
    output/report.md     分析报告（含结论、明细表、图表）
    output/*.png         各维度图表
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# 保证从任意目录运行都能 import src
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import analysis, config, report, viz  # noqa: E402
from src.cleaner import clean  # noqa: E402
from src.loader import DataLoadError, load_clean_ready  # noqa: E402


def make_console_safe() -> None:
    """让控制台输出不再因为编码问题崩掉。

    Windows 中文控制台默认 GBK，遇到 emoji 这类字符会直接抛 UnicodeEncodeError：
    本项目就因此在「[5/5] 生成报告」前一步整段崩过——分析和图表都算完了，报告却没落地。
    这里把编码错误策略改成 replace：编不出来的字符退化成 '?'，
    而不是让整个流程白跑一遍。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass  # 被重定向/包装过的流没有 reconfigure，忽略即可


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="就业 / 招聘数据分析：清洗 → 统计 → 检验 → 可视化 → 报告（默认数据源：Boss 岗位数据）"
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="指定数据文件路径（默认：Boss 岗位数据 → 演示数据；问卷 CSV 必须显式指定）",
    )
    parser.add_argument("--no-charts", action="store_true", help="跳过图表生成")
    parser.add_argument("--top", type=int, default=5, help="专业维度取前几名，默认 5")
    parser.add_argument(
        "--check-data",
        action="store_true",
        help="只检查数据文件（列名/规模/缺失值/类型），不跑分析",
    )
    return parser.parse_args()


def check_data(path: str | None) -> int:
    """检查数据文件是否可用，并打印诊断信息。

    手动放入数据后先跑这个命令，能快速定位「列名不对」「数据类型不对」等问题。
    """
    from src import config as cfg
    from src.loader import load_data

    try:
        df, info = load_data(path)
    except DataLoadError as exc:
        print("[错误] 数据检查失败：\n", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1

    print("=" * 62)
    print("数据文件检查")
    print("=" * 62)
    print(f"文件：{info.path}")
    print(f"性质：{info.label}")
    print(f"规模：{info.rows} 行 × {info.cols} 列")
    print()

    print("-- 列名与类型 --")
    print(f"{'列名':<12}{'类型':<12}{'缺失数':<8}{'样例值'}")
    print("-" * 62)
    for col in df.columns:
        sample = df[col].dropna()
        sample_val = str(sample.iloc[0]) if not sample.empty else ""
        if len(sample_val) > 22:
            sample_val = sample_val[:22] + "..."
        print(f"{col:<12}{str(df[col].dtype):<12}{int(df[col].isna().sum()):<8}{sample_val}")
    print()

    print("-- 必需列检查 --")
    missing = [c for c in cfg.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        print(f"[X] 缺少必需列：{missing}")
        print(f"    当前实际列名：{df.columns.tolist()}")
        print("    请按 data/README.md 的字段说明改名后重试。")
        return 1
    print(f"[OK] {len(cfg.REQUIRED_COLUMNS)} 个必需列齐全")

    print()
    print("-- 数值列可转换性检查 --")
    for col in (cfg.COL_SALARY, cfg.COL_SATISFACTION):
        converted = pd.to_numeric(df[col], errors="coerce")
        bad = int(converted.isna().sum() - df[col].isna().sum())
        if bad > 0:
            samples = df.loc[converted.isna() & df[col].notna(), col].head(5).tolist()
            print(f"[!] {col}：{bad} 个值无法转成数字，例如 {samples}")
        else:
            print(f"[OK] {col}：可正常转为数值")

    print()
    print("=" * 62)
    print("检查完成，可以运行：python main.py")
    return 0


def main() -> int:
    make_console_safe()
    args = parse_args()

    if args.check_data:
        return check_data(args.data)

    # 1) 加载
    try:
        df_raw, info = load_clean_ready(args.data)
    except DataLoadError as exc:
        print("[错误] 数据加载失败：\n", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 1

    print(f"[1/5] 数据加载完成：{info.describe()}")

    # 2) 清洗
    df_clean, cleaning_report = clean(df_raw)
    print(f"[2/5] 数据清洗完成：{cleaning_report.original_rows} → {cleaning_report.final_rows} 行")

    # 3) 分析（--top 直接贯通「专业 TOP」与「专业 × 城市」交叉）
    results = analysis.run_all(df_clean, top_n=args.top)
    print(f"[3/5] 分析完成（专业维度取前 {args.top}）")

    # 4) 可视化
    charts = {}
    if not args.no_charts:
        config.setup_chinese_font()
        charts = viz.render_all(df_clean, results)
        print(f"[4/5] 已生成 {len(charts)} 张图表 → {config.OUTPUT_DIR}")
    else:
        print("[4/5] 已跳过图表生成")

    # 5) 报告
    conclusions = report.build_conclusions(results)
    report.print_console_report(info, cleaning_report, results, conclusions, charts)
    md_path = report.write_markdown_report(info, cleaning_report, results, conclusions, charts)
    print(f"[5/5] 报告已生成：{md_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
