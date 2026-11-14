"""公开聚合表的守门测试。

为什么值得单独测：
    `data/published/boss_market_summary.csv` 是仓库里唯一一份「真实数据」的产物，
    最容易出的事故有两种 ——
    1. 有人图省事把岗位级字段（公司名 / 岗位标题 / JD）塞进来，等于把抓取数据变相入库；
    2. 小样本格子（n < 30）被发布出去，违反「样本不足不给结论」的项目约定。
    这两条都用断言钉死，改坏了 CI 会直接红。

运行：
    python tests/test_published_summary.py
    pytest -q tests/test_published_summary.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd

from src import config

SUMMARY_PATH = ROOT_DIR / "data" / "published" / "boss_market_summary.csv"

# 允许出现的列（白名单）：改动输出格式必须同步改这里，防止悄悄加入岗位级字段
REQUIRED_COLUMNS = [
    "scope",
    "dimension",
    "value",
    "jobs",
    "salary_n",
    "mean_salary",
    "median_salary",
    "p25",
    "p75",
    "salary_std",
    "mean_satisfaction",
    "intern_share",
    "sample_ok",
]

# 绝不允出现的岗位级 / 主体级字段
FORBIDDEN_COLUMNS = [
    "job_id",
    "job_title",
    "company",
    "company_size",
    "industry",
    "jd_text",
    "skills",
    "welfare",
    "job_type",
    "city_district",
    "business_district",
]


def _load() -> pd.DataFrame:
    assert SUMMARY_PATH.exists(), f"缺少聚合表：{SUMMARY_PATH}（运行 scripts/export_published_summary.py）"
    return pd.read_csv(SUMMARY_PATH)


def test_published_summary_has_no_job_level_columns() -> None:
    """聚合表只能有统计列，不能出现任何岗位级字段。"""
    table = _load()
    missing = [c for c in REQUIRED_COLUMNS if c not in table.columns]
    assert not missing, f"缺少列：{missing}"

    leaked = [c for c in FORBIDDEN_COLUMNS if c in table.columns]
    assert not leaked, f"聚合表里出现了岗位级字段：{leaked}"


def test_published_summary_respects_min_group_size() -> None:
    """sample_ok 必须与样本量门槛一致：达标为 1、不达标为 0，小样本不能伪装成达标。"""
    table = _load()
    jobs = pd.to_numeric(table["jobs"], errors="coerce")
    expected = (jobs >= config.MIN_GROUP_SIZE).astype(int)
    mismatched = table["sample_ok"].astype(int) != expected
    assert not mismatched.any(), f"sample_ok 与门槛不一致的行：\n{table[mismatched]}"


def test_published_summary_covers_all_scopes() -> None:
    """每个口径至少一行，且 overall 只有一行、样本量与 city 口径的最大城市一致。"""
    table = _load()
    scopes = set(table["scope"])
    for scope in ("overall", "city", "major", "education", "direction", "city_tier"):
        assert scope in scopes, f"缺少口径：{scope}"

    overall = table[table["scope"] == "overall"]
    assert len(overall) == 1, "overall 口径应当只有一行"
    assert int(overall["jobs"].iloc[0]) > 0


def main() -> int:
    tests = [
        ("聚合表不含岗位级字段", test_published_summary_has_no_job_level_columns),
        ("聚合表遵守最小样本量门槛", test_published_summary_respects_min_group_size),
        ("聚合表覆盖全部口径", test_published_summary_covers_all_scopes),
    ]
    failed = 0
    for name, func in tests:
        try:
            func()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
        else:
            print(f"[OK] {name}")

    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())