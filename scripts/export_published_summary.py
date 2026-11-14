"""导出可公开入库的聚合统计表（不含公司名、不含单条岗位记录）。

为什么要有这个脚本
------------------
原始抓取数据（`data/raw/boss/`）不入库：里面是站点内容与公司名，体量大、也可能持续增长。
但仓库需要能证明「分析跑在真实数据上」，所以把清洗后的数据聚合成一份小 CSV：
只有维度取值 + 样本量 + 薪资分布 + 满意度，没有任何公司名或岗位级记录。
`sample_ok` 标记沿用 config.MIN_GROUP_SIZE（默认 30），与报告、热力图的口径一致。

用法
----
    python scripts/export_published_summary.py
    python scripts/export_published_summary.py --data data/raw/xxx.csv --out data/published/xxx.csv

输出列
------
    scope              统计口径：overall / city / city_tier / major / education / direction / city_major
    dimension          维度名（与报告里的中文维度一致）
    value              维度取值（city_major 形如「数据分析@北京」）
    jobs               岗位数（清洗后）
    salary_n           有薪资解析值的岗位数
    mean_salary        平均期望薪资（元）；适配层里它是岗位薪资区间中值
    median_salary      P50（元）
    p25 / p75          四分位（元，线性插值口径）
    salary_std         标准差（元）
    mean_satisfaction  平均满意度（0~5；Boss 数据源下是代理分）
    intern_share       实习岗占比（0~1）
    sample_ok          样本量是否达到门槛（1 = 达标）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd

from src import cleaner, config, loader, report, stats


def _row(scope: str, dimension: str, value: object, frame: pd.DataFrame) -> dict:
    """把一个分组的统计量摊成一行。"""
    salary = pd.to_numeric(frame[config.COL_SALARY], errors="coerce")
    desc = stats.describe(salary)
    quartiles = stats.quantiles(salary)

    satisfaction = pd.to_numeric(frame[config.COL_SATISFACTION], errors="coerce")
    intern = frame[config.COL_INTERN].astype(str).str.strip()
    jobs = int(len(frame))

    return {
        "scope": scope,
        "dimension": dimension,
        "value": str(value),
        "jobs": jobs,
        "salary_n": int(desc["样本量"]),
        "mean_salary": desc["均值"],
        "median_salary": desc["中位数"],
        "p25": quartiles["p25"],
        "p75": quartiles["p75"],
        "salary_std": desc["标准差"],
        "mean_satisfaction": round(float(satisfaction.mean()), 3) if satisfaction.notna().any() else None,
        "intern_share": round(float((intern == "是").mean()), 3),
        "sample_ok": int(jobs >= config.MIN_GROUP_SIZE),
    }


def build_summary(clean: pd.DataFrame) -> pd.DataFrame:
    """把清洗后的数据聚合成一张长表（每个维度一组行）。"""
    rows: list[dict] = [_row("overall", "全部", "全部岗位", clean)]

    single_dimensions = (
        ("city", config.COL_CITY),
        ("major", config.COL_MAJOR),
        ("education", config.COL_EDU),
        ("direction", config.COL_DIRECTION),
    )
    for scope, column in single_dimensions:
        if column not in clean.columns:
            continue
        for value, group in clean.groupby(column, dropna=False):
            rows.append(_row(scope, column, value, group))

    # 城市层级（一线 / 新一线 / 其他）：与报告里的「一线 vs 新一线」对比同一口径
    city = clean[config.COL_CITY].astype(str).str.strip()
    labels = pd.Series("其他城市", index=clean.index, dtype=object)
    labels[city.isin(set(config.TIER1_CITIES))] = "一线城市"
    labels[city.isin(set(config.NEW_TIER1_CITIES))] = "新一线城市"
    for value, group in clean.groupby(labels):
        rows.append(_row("city_tier", "城市层级", value, group))

    # 城市 × 专业：只保留达到样本量门槛的格子（小样本均值不发布）
    if config.COL_MAJOR in clean.columns and config.COL_CITY in clean.columns:
        grouped = clean.groupby([config.COL_MAJOR, config.COL_CITY], dropna=False)
        for (major, city_name), group in grouped:
            if len(group) >= config.MIN_GROUP_SIZE:
                rows.append(_row("city_major", "城市×专业", f"{major}@{city_name}", group))

    table = pd.DataFrame(rows)
    table = table.sort_values(["scope", "jobs"], ascending=[True, False]).reset_index(drop=True)
    return table


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导出可公开入库的聚合统计表（聚合量，不含公司名与单条岗位）",
    )
    parser.add_argument("--data", default=None, help="数据文件（默认：Boss 岗位数据 → 演示数据）")
    parser.add_argument(
        "--out",
        # 注意：config.DATA_DIR 指向 data/raw（原始数据目录），可公开的聚合结果放在 data/published
        default=str(ROOT_DIR / "data" / "published" / "boss_market_summary.csv"),
        help="输出 CSV（默认 data/published/boss_market_summary.csv）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    df, info = loader.load_data(args.data)
    clean, cleaning = cleaner.clean(df)

    table = build_summary(clean)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"[汇总] 数据：{info.label}｜{len(df)} 行 → 清洗后 {len(clean)} 行")
    print(f"[汇总] 指纹：{report._file_sha256(info.path, 12)}")
    print(f"[汇总] 剔除：无效薪资 {cleaning.invalid_salary_removed} 行，IQR 异常 {cleaning.outliers_removed} 行")
    print(f"[汇总] 输出：{len(table)} 行 × {len(table.columns)} 列 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())