"""只读审计：方向归类覆盖率与「其他」里到底是什么岗位。

用法：
    python scripts/audit_major_coverage.py                 # 审计 data/raw/boss_岗位数据集.csv
    python scripts/audit_major_coverage.py --data 某文件.csv --top 100

它只做三件事：
1. 报出每个方向各多少条、占比多少；
2. 报出「其他」里出现次数最多的标题（这是补关键词表的依据）；
3. 把未归类标题写到 ``notes/major_gaps_<日期>.txt``（notes/ 不入库）。

不修改任何数据文件，也不写报告。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd  # noqa: E402

from src import config  # noqa: E402
from src.sources import boss  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计岗位方向归类覆盖率（只读）")
    parser.add_argument("--data", default=str(config.BOSS_DATA_PATH), help="分析用 CSV 路径")
    parser.add_argument("--top", type=int, default=50, help="列出多少个未归类标题（默认 50）")
    parser.add_argument("--notes-dir", default=str(ROOT_DIR / "notes"), help="未归类标题的落盘目录")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.data)
    if not path.exists():
        print(f"[错误] 找不到数据文件：{path}")
        print("       先抓取并适配：python scripts/crawl_boss.py --cookie-file cookie.txt --to-csv")
        return 2

    frame = pd.read_csv(path, encoding="utf-8-sig")
    coverage = boss.major_coverage(frame)

    print(f"[审计] 数据：{path.name}｜{coverage['rows']} 条")
    print(
        f"[审计] 已归类 {coverage['classified']} 条（{coverage['coverage']:.1%}），"
        f"「其他」{coverage['unclassified']} 条（{coverage['other_share']:.1%}）"
    )

    counts = frame[config.COL_MAJOR].astype(str).value_counts()
    print("[审计] 各方向分布：")
    for name, count in counts.items():
        print(f"        {name:<6}{count:>6}  {count / len(frame):.1%}")

    salary = boss.salary_coverage(frame)
    print(f"[审计] 薪资可解析 {salary['parsed']}/{salary['rows']}（{salary['ratio']:.1%}）")

    titles = boss.unclassified_titles(frame, limit=args.top)
    if titles:
        notes_dir = Path(args.notes_dir)
        notes_dir.mkdir(parents=True, exist_ok=True)
        out = notes_dir / f"major_gaps_{datetime.now().strftime('%Y%m%d')}.txt"
        out.write_text("\n".join(titles) + "\n", encoding="utf-8")
        print(f"[审计] 「其他」Top {len(titles)} 已写入 {out}（notes/ 不入库）")
        print("[审计] Top 10：")
        for line in titles[:10]:
            print(f"        {line}")
    else:
        print("[审计] 「其他」为空，无需补词表。")

    if coverage["coverage"] is not None and coverage["coverage"] < config.MAJOR_COVERAGE_WARN:
        print(
            f"[提示] 归类覆盖率 {coverage['coverage']:.1%} 低于阈值 {config.MAJOR_COVERAGE_WARN:.0%}，"
            "建议按上面的标题补充 MAJOR_RULES。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())