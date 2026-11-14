# -*- coding: utf-8 -*-
"""SQL 数据层测试：schema 引导、幂等装载、门禁拦截、查询可跑。

**全部走 SQLite 镜像后端**（零依赖）：本机可能没装 MySQL，而 SQLite 镜像的表名、
列名、约束语义与 MySQL 版一致，足以验证与数据库无关的部分 —— 装载逻辑、幂等性、
门禁、视图与查询文件。MySQL 侧的验证交给 CI 的 service container。

跑法（两种等价）：
    python tests/test_sql_layer.py
    python -m pytest -q tests/
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from src import analysis, cleaner, config, db, loader, report
from src.sources import boss

# 岗位数据源的离线样例（29 行，含 1 条重复 job_id）
SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "sample" / "boss_jobs_sample.jsonl"

# 装载后应当能跑出结果的查询文件（其余查询靠 CI 的 mysql job 验证）
QUERY_CASES = ("02_salary_percentiles_cross_check", "03_pareto_distribution")

TABLE_NAMES = (
    "ods_employment_survey",
    "dim_major",
    "dim_city",
    "dim_education",
    "dim_direction",
    "dim_industry",
    "dwd_survey_clean",
    "ads_run_meta",
    "ads_group_stats",
    "ads_major_city_matrix",
    "ads_comparisons",
    "ads_quality_checks",
    "ads_weight_sensitivity",
)

VIEW_NAMES = (
    "v_fact_long",
    "v_salary_percentiles_nearest_rank",
    "v_group_share",
    "v_latest_run",
    "v_comparison_dashboard",
    "v_quality_summary",
    "v_quality_flagged",
    "v_city_major_gap",
)


def _fixture_frame(rows: int = 120, seed: int = 7) -> pd.DataFrame:
    """确定性数据：故意包含缺失值、无效薪资、以及不是城市的「无」。"""
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {
            config.COL_ID: [f"S{index:04d}" for index in range(rows)],
            config.COL_GENDER: rng.choice(["男", "女"], rows),
            config.COL_EDU: rng.choice(["本科", "硕士", "博士"], rows, p=[0.6, 0.3, 0.1]),
            config.COL_MAJOR: rng.choice(
                ["计算机", "电子信息", "金融", "临床医学", "机械工程"], rows
            ),
            config.COL_DIRECTION: rng.choice(
                ["就业", "考研", "待业"], rows, p=[0.7, 0.2, 0.1]
            ),
            config.COL_INDUSTRY: rng.choice(["互联网", "制造业", "无"], rows),
            config.COL_SALARY: rng.integers(4000, 15000, rows),
            config.COL_CITY: rng.choice(
                ["上海", "北京", "广州", "深圳", "武汉", "南京", "无"], rows
            ),
            config.COL_SATISFACTION: rng.integers(1, 6, rows).astype(float),
            config.COL_INTERN: rng.choice(["是", "否"], rows),
        }
    )
    frame.loc[0, config.COL_SALARY] = np.nan   # 缺失 → 中位数填充
    frame.loc[1, config.COL_SALARY] = -500     # 非正 → 清洗阶段剔除
    frame.loc[2, config.COL_INTERN] = np.nan   # 缺失 → 众数填充
    return frame


class _Fixture:
    """一份临时 CSV + 跑完加载/清洗/分析的结果（测试之间互不影响）。"""

    def __init__(self, rows: int = 120, seed: int = 7) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "demo_sql_layer.csv"
        self.frame = _fixture_frame(rows, seed)
        self.frame.to_csv(self.path, index=False, encoding="utf-8-sig")

        self.raw, self.info = loader.load_data(self.path)
        self.clean, self.cleaning = cleaner.clean(self.raw)
        self.results = analysis.run_all(self.clean, top_n=5)

    def connect(self):
        return db.connect("sqlite")

    def loaded(self):
        """引导 schema 并装载一次，返回 (连接, 装载结果)。"""
        conn = self.connect()
        db.ensure_schema(conn)
        outcome = db.load_run(
            self.raw,
            self.clean,
            self.results,
            self.info,
            self.cleaning,
            connection=conn,
            bootstrap=False,
        )
        return conn, outcome

    def close(self) -> None:
        self.temp.cleanup()


class _JobFixture:
    """岗位数据（离线样例）→ 清洗 → 装载，专门验证岗位市场层。"""

    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "boss_岗位数据集.csv"
        boss.build_frame(SAMPLE_PATH).to_csv(self.path, index=False, encoding="utf-8-sig")

        self.raw, self.info = loader.load_data(self.path)
        self.clean, self.cleaning = cleaner.clean(self.raw)
        self.results = analysis.run_all(self.clean, top_n=5)

    def loaded(self):
        conn = db.connect("sqlite")
        db.ensure_schema(conn)
        db.load_run(
            self.raw,
            self.clean,
            self.results,
            self.info,
            self.cleaning,
            connection=conn,
            bootstrap=False,
        )
        return conn

    def close(self) -> None:
        self.temp.cleanup()


def test_job_layer_loads_posting_skills() -> None:
    """岗位数据装载：事实表 / 公司维表 / 技能维表 / 技能桥表都要有行，且可重复装载。"""
    fixture = _JobFixture()
    try:
        conn = fixture.loaded()

        def count(table: str) -> int:
            return int(db.query(f"SELECT COUNT(*) AS n FROM {table}", connection=conn)[0]["n"])

        jobs = count("dwd_job_posting")
        assert jobs >= 20, f"岗位事实表行数过少：{jobs}"
        assert count("dim_company") >= 5
        assert count("dim_skill") > 0
        assert count("dwd_job_skill") > 0
        assert count("ods_job_posting") == jobs

        ranks = {
            int(row["experience_rank"])
            for row in db.query("SELECT DISTINCT experience_rank FROM dwd_job_posting", connection=conn)
        }
        assert ranks <= {0, 1, 2, 3, 4, 5, 9}, f"出现越界序数：{sorted(ranks - {0, 1, 2, 3, 4, 5, 9})}"

        # 重复装载：job_id 是主键，事实表行数不应增加（幂等）
        db.load_run(
            fixture.raw,
            fixture.clean,
            fixture.results,
            fixture.info,
            fixture.cleaning,
            connection=conn,
            bootstrap=False,
        )
        assert count("dwd_job_posting") == jobs
    finally:
        fixture.close()


def _table_counts(conn) -> dict[str, int]:
    return {
        table: int(db.query(f"SELECT COUNT(*) AS n FROM {table}", connection=conn)[0]["n"])
        for table in TABLE_NAMES
    }


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_schema_and_views_bootstrap() -> None:
    fixture = _Fixture(rows=40)
    try:
        conn = fixture.connect()
        db.ensure_schema(conn)

        tables = {
            row["name"]
            for row in db.query(
                "SELECT name FROM sqlite_master WHERE type = 'table'", connection=conn
            )
        }
        views = {
            row["name"]
            for row in db.query(
                "SELECT name FROM sqlite_master WHERE type = 'view'", connection=conn
            )
        }
        missing = sorted(set(TABLE_NAMES) - tables)
        assert not missing, f"schema 缺少表：{missing}"
        missing_views = sorted(set(VIEW_NAMES) - views)
        assert not missing_views, f"视图没建出来：{missing_views}"

        # 视图本身要能查（空表也应该返回 0 行，而不是报错）
        assert db.query("SELECT * FROM v_fact_long", connection=conn) == []
        # schema 引导必须可重复执行（幂等）
        db.ensure_schema(conn)
    finally:
        fixture.close()


def test_load_run_writes_every_layer() -> None:
    fixture = _Fixture()
    try:
        conn, outcome = fixture.loaded()
        assert fixture.cleaning.invalid_salary_removed == 1, "无效薪资那行应该被剔除"
        assert not outcome.blocked, "\n".join(outcome.summary_lines())

        counts = _table_counts(conn)
        assert counts["ods_employment_survey"] == len(fixture.frame)
        assert counts["dwd_survey_clean"] == len(fixture.clean)
        assert counts["ads_run_meta"] == 1
        assert counts["ads_group_stats"] > 0
        assert counts["ads_major_city_matrix"] > 0
        assert counts["ads_comparisons"] == len(fixture.results["comparisons"])
        assert counts["ads_quality_checks"] == len(fixture.results["quality"]["checks"])
        assert counts["ads_weight_sensitivity"] == len(
            fixture.results["recommend_sensitivity"]["rows"]
        )
    finally:
        fixture.close()


def test_load_is_idempotent() -> None:
    fixture = _Fixture(rows=60)
    try:
        conn, outcome = fixture.loaded()
        first = _table_counts(conn)

        again = db.load_run(
            fixture.raw,
            fixture.clean,
            fixture.results,
            fixture.info,
            fixture.cleaning,
            connection=conn,
            run_id=outcome.run_id,  # 同一批结果重复装载
            bootstrap=False,
        )
        second = _table_counts(conn)
        assert again.run_id == outcome.run_id
        assert first == second, f"重复装载改变了行数：{first} → {second}"
    finally:
        fixture.close()


def test_city_placeholder_becomes_unknown_member() -> None:
    fixture = _Fixture(rows=60)
    try:
        conn, _outcome = fixture.loaded()
        cities = [
            str(row["city"]) for row in db.query("SELECT city FROM dim_city", connection=conn)
        ]
        assert db.UNKNOWN_DIM_VALUE in cities, "占位城市应归到「未指定」成员上"
        assert "无" not in cities, "「无」不是一个城市，不允许进维表"

        orphan = db.query(
            "SELECT COUNT(*) AS n FROM dwd_survey_clean f "
            "LEFT JOIN dim_city c ON c.city_key = f.city_key "
            "WHERE c.city_key IS NULL",
            connection=conn,
        )[0]["n"]
        assert int(orphan) == 0, "事实表里出现了对不上维表的城市键"
    finally:
        fixture.close()


def test_checks_block_bad_rows() -> None:
    """坏数据有两道防线：schema 的 CHECK 约束，和装载门禁的语义检查。"""
    fixture = _Fixture(rows=60)
    try:
        conn, _outcome = fixture.loaded()

        # 第一道：CHECK 约束直接拒绝「薪资 <= 0」这种一眼可见的坏数据
        rejected = False
        try:
            conn.execute(
                "UPDATE dwd_survey_clean SET expected_salary = -1 "
                "WHERE respondent_uid = (SELECT MIN(respondent_uid) FROM dwd_survey_clean)"
            )
            conn.commit()
        except Exception:  # noqa: BLE001 - 不同后端的异常类型不同，只看是否被拒绝
            conn.rollback()
            rejected = True
        assert rejected, "CHECK 约束应该直接拒绝负薪资"

        # 第二道：约束管不到的语义不变量（小样本均值泄漏）由门禁兜住
        conn.execute(
            "UPDATE ads_major_city_matrix SET mean_salary = 12345 WHERE insufficient = 1"
        )
        conn.commit()
        checks = {check["check_name"]: check for check in db.run_checks(conn)}
        item = checks["ads_matrix_small_sample_leaked"]
        assert item["severity"] == "阻断"
        assert int(item["violations"]) >= 1, "小样本格子被填了均值，门禁必须拦住"
    finally:
        fixture.close()


def test_query_files_run_after_load() -> None:
    fixture = _Fixture()
    try:
        conn, _outcome = fixture.loaded()
        for name in QUERY_CASES:
            rows = db.run_query_file(name, connection=conn)
            assert rows, f"查询 {name} 装载后仍返回空结果"
    finally:
        fixture.close()


def test_group_stats_excludes_placeholder_values() -> None:
    fixture = _Fixture(rows=60)
    try:
        table = fixture.results["group_stats"]
        assert set(table["dimension"]) >= {"education", "major", "city", "direction", "industry"}
        assert (table["sample_size"] > 0).all()

        city_values = set(table.loc[table["dimension"] == "city", "group_value"])
        assert "无" not in city_values and db.UNKNOWN_DIM_VALUE not in city_values
        industry_values = set(table.loc[table["dimension"] == "industry", "group_value"])
        assert "无" not in industry_values
    finally:
        fixture.close()


def test_sql_constants_match_config() -> None:
    """SQL 文件里的阈值/城市清单不许与 src/config.py 漂移。"""
    top_n_sql = (db.QUERY_DIR / "01_top_n_cities_by_major.sql").read_text(encoding="utf-8")
    assert f">= {config.MIN_GROUP_SIZE}" in top_n_sql

    pivot_sql = (db.QUERY_DIR / "05_major_city_pivot.sql").read_text(encoding="utf-8")
    for city in config.MAIN_CITIES:
        assert city in pivot_sql, f"05 的行转列清单少了城市：{city}"

    checks_sql = db.CHECKS_SQL.read_text(encoding="utf-8")
    assert f"sample_size < {config.MIN_GROUP_SIZE}" in checks_sql


def test_fingerprint_matches_report() -> None:
    """装载层的指纹口径必须与报告里打印的指纹一致（同一个值，两种实现）。"""
    fixture = _Fixture(rows=40)
    try:
        fingerprint = db._data_fingerprint(fixture.path)
        assert fingerprint == report._file_sha256(fixture.path)
        assert len(fingerprint) == 12
    finally:
        fixture.close()


def test_rebootstrap_after_load_is_safe() -> None:
    """带数据的库上重新引导 schema：DROP 顺序写错就会在这里炸出来。

    SQLite（foreign_keys=ON）删被引用的维表会触发外键检查，MySQL 直接报 errno 3730，
    所以两个 schema 文件都先清事实表与结果表、再清维表。CI 的 mysql job 也会走这条路
    （service container 先执行过一次 schema，装载脚本再引导一次）。
    """
    fixture = _Fixture(rows=60)
    try:
        conn, _outcome = fixture.loaded()
        before = _table_counts(conn)
        assert before["dwd_survey_clean"] > 0, "前置条件：库里得有数据"

        db.ensure_schema(conn)

        after = _table_counts(conn)
        assert after["dwd_survey_clean"] == 0, "重新引导后事实表应为空"
        assert after["dim_city"] == 0, "重新引导后维表应为空"
        assert after["ods_employment_survey"] == 0, "重新引导后 ODS 应为空"

        # 重建之后必须还能照常装载（第二次装载与新库一样顺）
        outcome = db.load_run(
            fixture.raw,
            fixture.clean,
            fixture.results,
            fixture.info,
            fixture.cleaning,
            connection=conn,
            bootstrap=False,
        )
        assert not outcome.blocked, "\n".join(outcome.summary_lines())
        assert _table_counts(conn)["dwd_survey_clean"] == len(fixture.clean)
    finally:
        fixture.close()


def main() -> int:
    tests = [
        ("schema 与视图能引导（可重复执行）", test_schema_and_views_bootstrap),
        ("装载把 ODS/DWD/ADS 三层都写满", test_load_run_writes_every_layer),
        ("岗位市场层装载（事实/维表/桥表/幂等）", test_job_layer_loads_posting_skills),
        ("重复装载幂等（行数不翻倍）", test_load_is_idempotent),
        ("带数据时重新引导 schema 也安全（DROP 顺序）", test_rebootstrap_after_load_is_safe),
        ("占位城市归到「未指定」成员", test_city_placeholder_becomes_unknown_member),
        ("门禁能拦住坏数据（负薪资）", test_checks_block_bad_rows),
        ("查询文件在装载后可执行", test_query_files_run_after_load),
        ("分组统计排除占位值", test_group_stats_excludes_placeholder_values),
        ("SQL 常量与 config 不漂移", test_sql_constants_match_config),
        ("数据指纹与报告口径一致", test_fingerprint_matches_report),
    ]

    failed = 0
    print("SQL 数据层测试（SQLite 镜像后端）")
    print("-" * 60)
    for name, func in tests:
        try:
            func()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {name}：{exc}")
        except Exception as exc:  # noqa: BLE001 - 测试运行器要报告任何异常
            failed += 1
            print(f"[ERROR] {name}：{type(exc).__name__}: {exc}")
        else:
            print(f"[OK]   {name}")
    print("-" * 60)
    print(f"{len(tests) - failed} / {len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())