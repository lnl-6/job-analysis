"""跨日快照对比：找出两次抓取之间「涨了/跌了/消失了」的方向与城市。

用法：
    python scripts/compare_snapshots.py                      # 自动比较最近两次快照
    python scripts/compare_snapshots.py <旧目录> <新目录>      # 指定两次快照目录

快照目录形如 data/published/boss/2026-11-23/，里面是 boss_market_summary.csv。
对比口径：按 (scope, dimension, value) 对齐，报告 岗位数 / 均值薪资 / 中位数 的差值，
以及「新增」「消失」的分组。纯描述性，不做显著性判断（样本量不同，跨日比较本就不能当因果）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
PUBLISHED_DIR = ROOT_DIR / "data" / "published" / "boss"
KEY = ["scope", "dimension", "value"]
METRICS = ["jobs", "mean_salary", "median_salary"]


def _snapshot_dirs() -> list[Path]:
    if not PUBLISHED_DIR.exists():
        return []
    return sorted(path for path in PUBLISHED_DIR.iterdir() if path.is_dir())


def compare(old: Path, new: Path, limit: int = 15) -> pd.DataFrame:
    """对齐两次快照，返回变化表（按 |均值变化| 降序）。"""
    old_df = pd.read_csv(old / "boss_market_summary.csv")
    new_df = pd.read_csv(new / "boss_market_summary.csv")
    merged = old_df[KEY + METRICS].merge(
        new_df[KEY + METRICS],
        on=KEY,
        how="outer",
        suffixes=("_旧", "_新"),
        indicator=True,
    )

    merged["岗位数变化"] = (merged["jobs_新"].fillna(0) - merged["jobs_旧"].fillna(0)).astype(int)
    merged["均值变化"] = merged["mean_salary_新"] - merged["mean_salary_旧"]
    merged["状态"] = merged["_merge"].map({"both": "两期都有", "left_only": "本期消失", "right_only": "本期新增"})
    merged = merged.drop(columns=["_merge"])
    merged["均值变化"] = merged["均值变化"].round(0)
    merged = merged.sort_values("均值变化", key=lambda s: s.abs(), ascending=False)
    return merged.dropna(subset=["scope", "value"]).head(limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="比较两次公开聚合快照的差异")
    parser.add_argument("old", nargs="?", help="旧快照目录（默认：倒数第二次）")
    parser.add_argument("new", nargs="?", help="新快照目录（默认：最近一次）")
    parser.add_argument("--limit", type=int, default=15, help="显示前 N 行（默认 15）")
    args = parser.parse_args(argv)

    if args.old and args.new:
        old, new = Path(args.old), Path(args.new)
    else:
        dirs = _snapshot_dirs()
        if len(dirs) < 2:
            print(f"[快照] 至少需要两次快照才能对比，当前只有 {len(dirs)} 次（{PUBLISHED_DIR}）")
            return 1
        old, new = dirs[-2], dirs[-1]

    table = compare(old, new, args.limit)
    print(f"[快照] 旧：{old.name}　新：{new.name}")
    if table.empty:
        print("[快照] 两次快照没有可比的分组")
        return 0
    print(table.to_string(index=False))
    moved = table["均值变化"].abs().gt(0).sum()
    print(f"\n[快照] 共 {len(table)} 组进入表格，其中 {moved} 组均值发生变化。")
    print("[快照] 注意：两次采样独立，跨日差异只能描述「这批发出去的岗位变了」，不能当成薪资涨跌的趋势。")
    return 0


if __name__ == "__main__":
    sys.exit(main())