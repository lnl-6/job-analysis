# -*- coding: utf-8 -*-
"""SQL 数据层：schema 引导、幂等装载、阻断式门禁。

这一层只做三件事，别的都不做：
    1) 引导 schema（MySQL 权威版 sql/00_schema.sql / SQLite 本地镜像 sql/schema_sqlite.sql）；
    2) 把 Python 侧**已经算好**的结果原样搬进 ADS 表；
    3) 执行阻断式门禁（sql/quality_checks.sql）。

为什么不在这一层「顺手算一下」：统计量、判定文案、样本量门槛只在
src/stats.py / src/analysis.py / src/quality.py 里各有一份实现。
装载层一旦自己再算一遍，报告、数据库、BI 三处就会各说各话 ——
这是数仓项目最常见、也最难解释的一类事故，本仓库直接用「只搬不算」规避。

后端：
    mysql  —— 权威后端（MySQL 8），需要 PyMySQL，CI 用 service container 起库；
    sqlite —— 本地零依赖镜像（表名、列名、约束语义与 MySQL 版一致），
              用于在没装 MySQL 的机器上验证视图、查询与幂等性。

幂等：
    * 维表 upsert（MySQL: ON DUPLICATE KEY UPDATE / SQLite: ON CONFLICT DO UPDATE）；
    * ODS 按 load_batch、事实表与结果表按 run_id 先删后插 ——
      同一份数据重复装载不会翻倍，同一批结果重复装载不会累积。
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from src import config
from src.cleaner import CleaningReport
from src.loader import DataSourceInfo

# ---------------- 路径与常量 ----------------
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
MYSQL_SCHEMA = SQL_DIR / "00_schema.sql"
SQLITE_SCHEMA = SQL_DIR / "schema_sqlite.sql"
VIEWS_SQL = SQL_DIR / "01_views.sql"
CHECKS_SQL = SQL_DIR / "quality_checks.sql"
QUERY_DIR = SQL_DIR / "queries"
# 岗位市场层（Boss 数据源）的扩展 schema：依赖 dim_city / dim_industry，所以在基础 schema 之后执行
JOB_SCHEMA_SQL = SQL_DIR / "02_job_market.sql"
JOB_SCHEMA_SQLITE = SQL_DIR / "schema_job_sqlite.sql"

BACKENDS = ("mysql", "sqlite")

# 学历序号：写进 dim_education.education_rank，供 SQL 侧 ORDER BY 使用
# （与 sql/00_schema.sql 的列注释一致：1=大专 2=本科 3=硕士 4=博士）。
EDUCATION_RANK = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}
EDUCATION_RANK_UNKNOWN = 99  # 没列出来的学历排在最后，不瞎猜序号

# 事实表不允许为空的维度（城市）在维表里的统一成员：占位值「无 / 未填」
# 会被归并到它上面，避免 BI 里出现一个叫「无」的城市。
UNKNOWN_DIM_VALUE = "未指定"

# ODS 层列映射：目标列 → 源数据列（源文件缺某列时写 NULL，并在返回值里说明）
ODS_COLUMN_MAP = {
    "respondent_id": config.COL_ID,
    "gender": config.COL_GENDER,
    "major": config.COL_MAJOR,
    "education": config.COL_EDU,
    "direction": config.COL_DIRECTION,
    "industry": config.COL_INDUSTRY,
    "expected_salary": config.COL_SALARY,
    "city": config.COL_CITY,
    "satisfaction": config.COL_SATISFACTION,
    "has_internship": config.COL_INTERN,
}
ODS_COLUMNS = ["load_batch", "source_row_no", *ODS_COLUMN_MAP]

# 维度装载计划：(维表, 维表列, 事实表外键列, 是否允许为空, 需要归并的占位值)
DIMENSION_PLANS = (
    ("dim_major", config.COL_MAJOR, "major_key", False, ()),
    ("dim_education", config.COL_EDU, "education_key", False, ()),
    ("dim_direction", config.COL_DIRECTION, "direction_key", False, ()),
    ("dim_city", config.COL_CITY, "city_key", False, config.CITY_UNSPECIFIED),
    ("dim_industry", config.COL_INDUSTRY, "industry_key", True, config.INDUSTRY_UNSPECIFIED),
)


class DatabaseError(RuntimeError):
    """装载层异常：缺驱动、连不上、脚本格式不对、键对不上等。"""


@dataclass(frozen=True)
class LoadResult:
    """一次装载的结果：写入了多少行、门禁有没有拦住。"""

    backend: str
    run_id: str
    rows: dict[str, int] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocked(self) -> list[dict[str, Any]]:
        """阻断级且确实有违规的检查项（非空即代表本次装载不合格）。"""
        return [
            check
            for check in self.checks
            if check.get("severity") == "阻断" and int(check.get("violations") or 0) > 0
        ]

    def summary_lines(self) -> list[str]:
        lines = [f"后端：{self.backend}    运行编号：{self.run_id}", "写入行数："]
        lines += [f"  {table:<24}{count:>7} 行" for table, count in sorted(self.rows.items())]
        flagged = [c for c in self.checks if int(c.get("violations") or 0) > 0]
        lines.append(f"门禁：共 {len(self.checks)} 项检查，有违规 {len(flagged)} 项")
        lines += [
            f"  [{c['severity']}] {c['check_name']}：{c['violations']} —— {c['detail']}"
            for c in flagged
        ]
        return lines


# =============================================================================
# 连接
# =============================================================================
class _SqliteConnection:
    """sqlite3 的薄封装：统一 execute / executemany / executescript 与占位符风格。"""

    backend = "sqlite"

    def __init__(self, path: str | Path = ":memory:") -> None:
        import sqlite3

        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        if params:
            return self.conn.execute(sql, params)
        return self.conn.execute(sql)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        self.conn.executemany(sql, list(rows))

    def executescript(self, path: Path) -> None:
        # 与 MySQL 版共用同一批 .sql 文件：SQLite 不认 SET NAMES / USE 这类
        # 会话级语句，这里按语句切开、跳过它们再执行（其余语句原样保留）。
        statements = [
            statement
            for statement in _split_sql_statements(Path(path).read_text(encoding="utf-8"))
            if not statement.lstrip().upper().startswith(("SET ", "USE "))
        ]
        self.conn.executescript(";\n".join(statements) + ";")

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()


class _MySqlConnection:
    """PyMySQL 的薄封装：.sql 脚本由 _split_sql_statements 切成单条语句执行。"""

    backend = "mysql"

    def __init__(self, **kwargs: Any) -> None:
        pymysql = _import_mysql_driver()
        options = {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "password": "",
            "charset": "utf8mb4",
            "autocommit": False,
        }
        options.update({key: value for key, value in kwargs.items() if value is not None})
        self.options = options
        self.conn = pymysql.connect(**options)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        cursor = self.conn.cursor()
        # 只在真的有参数时走 PyMySQL 的 % 占位符格式化：
        # 否则 SQL 里的字面量 %（例如「缺失率超过 10%」）会被当成格式符，
        # 在 MySQL 上报 unsupported format character，而在 SQLite 上却正常。
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        return cursor

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        cursor = self.conn.cursor()
        cursor.executemany(sql, list(rows))

    def executescript(self, path: Path) -> None:
        cursor = self.conn.cursor()
        for statement in _split_sql_statements(Path(path).read_text(encoding="utf-8")):
            cursor.execute(statement)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()


def _import_mysql_driver() -> Any:
    try:
        import pymysql  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:  # pragma: no cover - 取决于本机环境
        raise DatabaseError(
            "缺少 MySQL 驱动 PyMySQL：请先执行 python -m pip install -r requirements.txt；"
            "如果本机没有 MySQL，改用 backend='sqlite'（本地零依赖镜像）。"
        ) from exc
    return pymysql


def connect(backend: str = "sqlite", **kwargs: Any) -> Any:
    """建立连接。mysql 额外接受 host/port/user/password/database 等参数。"""
    if backend == "sqlite":
        return _SqliteConnection(kwargs.get("path", ":memory:"))
    if backend == "mysql":
        return _MySqlConnection(**kwargs)
    raise DatabaseError(f"未知后端：{backend}（可选 {BACKENDS}）")


def ensure_schema(conn: Any) -> None:
    """引导 schema 与视图（可重复执行：脚本里都是 DROP ... IF EXISTS + CREATE）。"""
    is_mysql = conn.backend == "mysql"
    conn.executescript(MYSQL_SCHEMA if is_mysql else SQLITE_SCHEMA)
    conn.executescript(VIEWS_SQL)
    # 岗位市场层单独一份脚本（依赖 dim_city / dim_industry）；文件不存在时跳过，
    # 这样只关心问卷链路的人（或旧仓库副本）不会被它卡住。
    job_schema = JOB_SCHEMA_SQL if is_mysql else JOB_SCHEMA_SQLITE
    if job_schema.exists():
        conn.executescript(job_schema)
    conn.commit()


# =============================================================================
# SQL 文本工具
# =============================================================================
def _split_sql_statements(text: str) -> list[str]:
    """把 .sql 脚本切成单条语句（识别 ' " ` 三种引号、-- 与 /* */ 注释）。

    PyMySQL 默认不开 multi-statement，所以 MySQL 侧要自己切；
    SQLite 有 executescript，用不到这个函数。
    """
    statements: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, size = 0, len(text)

    while i < size:
        char = text[i]

        if quote is not None:
            buf.append(char)
            if char == quote:
                if i + 1 < size and text[i + 1] == quote:  # '' / "" / `` 转义
                    buf.append(text[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if char in "'\"`":
            quote = char
            buf.append(char)
            i += 1
            continue

        if char == "-" and i + 1 < size and text[i + 1] == "-":
            while i < size and text[i] != "\n":
                i += 1
            continue

        if char == "/" and i + 1 < size and text[i + 1] == "*":
            i += 2
            while i + 1 < size and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue

        if char == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf.clear()
            i += 1
            continue

        buf.append(char)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _last_select(path: Path) -> str:
    """取出脚本里的最后一条 SELECT/WITH 语句（门禁脚本前面还有 SET NAMES / USE）。"""
    statements = _split_sql_statements(Path(path).read_text(encoding="utf-8"))
    selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
    if not selects:
        raise DatabaseError(f"{path} 里没有找到 SELECT 语句")
    return selects[-1]


def _ph(conn: Any, count: int) -> str:
    """占位符：MySQL 用 %s，SQLite 用 ?。"""
    token = "%s" if conn.backend == "mysql" else "?"
    return ", ".join([token] * count)


def _insert_sql(conn: Any, table: str, columns: Sequence[str]) -> str:
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({_ph(conn, len(columns))})"
    )


def _upsert(conn: Any, table: str, columns: Sequence[str], key_columns: Sequence[str],
            rows: Sequence[Sequence[Any]]) -> int:
    """维表 upsert：按唯一键更新非键列；没有非键列时退化为「存在即忽略」。"""
    if not rows:
        return 0
    updatable = [c for c in columns if c not in key_columns]
    head = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({_ph(conn, len(columns))})"

    if conn.backend == "mysql":
        if updatable:
            tail = ", ".join(f"{c} = VALUES({c})" for c in updatable)
            sql = f"{head} ON DUPLICATE KEY UPDATE {tail}"
        else:
            sql = (
                f"INSERT IGNORE INTO {table} ({', '.join(columns)}) "
                f"VALUES ({_ph(conn, len(columns))})"
            )
    else:
        if updatable:
            conflict = ", ".join(key_columns)
            tail = ", ".join(f"{c} = excluded.{c}" for c in updatable)
            sql = f"{head} ON CONFLICT ({conflict}) DO UPDATE SET {tail}"
        else:
            sql = (
                f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
                f"VALUES ({_ph(conn, len(columns))})"
            )

    conn.executemany(sql, rows)
    return len(rows)


def _delete(conn: Any, table: str, where: str, params: Sequence[Any]) -> None:
    conn.execute(f"DELETE FROM {table} WHERE {where}", params)


# =============================================================================
# 元信息与工具
# =============================================================================
def _data_fingerprint(path: str | Path, length: int = 12) -> str:
    """数据文件指纹：SHA-256 前 12 位。

    口径必须与 src/report.py 打印的指纹一致（tests/test_sql_layer.py 会对两者做等价断言），
    这里重新实现是为了让装载层不依赖报告层的私有函数。
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:length]


def _new_run_id(fingerprint: str) -> str:
    """run_id = 数据指纹 + 时间戳（与 sql/00_schema.sql 的列注释一致）。"""
    return f"{fingerprint}-{datetime.now().strftime('%H%M%S')}"


def _module_version(name: str) -> str:
    try:
        module = __import__(name)
    except ModuleNotFoundError:  # pragma: no cover - 取决于本机环境
        return "n/a"
    return str(getattr(module, "__version__", "n/a"))


def _read_raw_text(path: str | Path) -> pd.DataFrame:
    """按原样字符串再读一遍 CSV：ODS 层要的是原始落地，不做类型推断。

    编码顺序与 src/loader.py 保持一致（utf-8-sig → gbk）。
    """
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise DatabaseError(f"无法按 utf-8-sig / gbk 读取数据文件：{path}")


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _dimension_values(
    series: pd.Series, excluded: Sequence[str], sentinel: str | None
) -> pd.Series:
    """维度列规范化：去首尾空白；占位值按 sentinel 归并（None = 该维度允许为空）。"""
    values = series.astype(str).str.strip()
    if not excluded:
        return values
    return values.where(~values.isin(excluded), sentinel)


# =============================================================================
# 装载：ODS / DWD
# =============================================================================
def _load_ods(conn: Any, raw: pd.DataFrame, load_batch: str) -> int:
    _delete(conn, "ods_employment_survey", f"load_batch = {_ph(conn, 1)}", (load_batch,))

    available = [column for column in ODS_COLUMN_MAP.values() if column in raw.columns]
    rows: list[tuple[Any, ...]] = []
    for position, (_, row) in enumerate(raw.iterrows(), start=1):
        values: list[Any] = [load_batch, position]
        for target, source in ODS_COLUMN_MAP.items():
            values.append(_text_or_none(row[source]) if source in available else None)
        rows.append(tuple(values))

    conn.executemany(_insert_sql(conn, "ods_employment_survey", ODS_COLUMNS), rows)
    return len(rows)


def _load_dimensions(conn: Any, clean: pd.DataFrame) -> dict[str, int]:
    """写入维表并返回各维表的取值个数。"""
    counts: dict[str, int] = {}
    for table, column, _fk, nullable, excluded in DIMENSION_PLANS:
        if column not in clean.columns:
            continue
        sentinel = None if nullable else UNKNOWN_DIM_VALUE
        values = _dimension_values(clean[column], excluded, sentinel)
        distinct = sorted({v for v in values.tolist() if isinstance(v, str) and v})

        if table == "dim_education":
            rows = [(value, EDUCATION_RANK.get(value, EDUCATION_RANK_UNKNOWN)) for value in distinct]
            counts[table] = _upsert(
                conn, table, ["education", "education_rank"], ["education"], rows
            )
        else:
            dimension_column = table.replace("dim_", "")
            rows = [(value,) for value in distinct]
            counts[table] = _upsert(conn, table, [dimension_column], [dimension_column], rows)
    return counts


def _dimension_key_map(conn: Any) -> dict[str, dict[str, int]]:
    """读出维表代理键：{源数据列名: {取值: 代理键}}。

    返回的 key 用源数据列名（中文），这样调用方可以直接拿清洗后的列去 map；
    维表里的列名是英文（major / city / …），两者不能混用 —— 这里显式区分。
    """
    mapping: dict[str, dict[str, int]] = {}
    for table, column, _fk, _nullable, _excluded in DIMENSION_PLANS:
        dimension_column = table.replace("dim_", "")
        key_column = f"{dimension_column}_key"
        rows = conn.execute(
            f"SELECT {dimension_column}, {key_column} FROM {table}"
        ).fetchall()
        mapping[column] = {str(value): int(key) for value, key in rows}
    return mapping


def _load_dwd(
    conn: Any,
    raw: pd.DataFrame,
    clean: pd.DataFrame,
    load_batch: str,
    key_map: dict[str, dict[str, int]],
) -> int:
    """写入事实表。source_row_no 用清洗后数据保留的原始行号（行被剔除也能对齐）。"""
    _delete(
        conn,
        "dwd_survey_clean",
        f"load_batch = {_ph(conn, 1)}",
        (load_batch,),
    )

    frame = pd.DataFrame(index=clean.index)
    frame["load_batch"] = load_batch
    frame["source_row_no"] = [int(label) + 1 for label in clean.index]
    frame["respondent_id"] = (
        raw[config.COL_ID] if config.COL_ID in raw.columns else pd.Series(index=raw.index, dtype=object)
    )
    frame["gender"] = clean[config.COL_GENDER].astype(str) if config.COL_GENDER in clean.columns else None

    for _table, column, fk, nullable, excluded in DIMENSION_PLANS:
        if column not in clean.columns:
            continue
        sentinel = None if nullable else UNKNOWN_DIM_VALUE
        values = _dimension_values(clean[column], excluded, sentinel)
        frame[fk] = values.map(key_map.get(column, {}))

    frame["expected_salary"] = clean[config.COL_SALARY].astype(float)
    frame["satisfaction"] = clean[config.COL_SATISFACTION].astype(float)
    frame["has_internship"] = (clean[config.COL_INTERN] == "是").astype(int)
    frame["salary_is_filled"] = _missing_flag(raw[config.COL_SALARY])
    frame["satisfaction_is_filled"] = _missing_flag(raw[config.COL_SATISFACTION])

    required = [fk for _t, _c, fk, nullable, _e in DIMENSION_PLANS if not nullable]
    broken = frame[required].isna().any(axis=1)
    if broken.any():
        sample = frame.loc[broken].head(3).to_dict("records")
        raise DatabaseError(
            f"有 {int(broken.sum())} 行的维度键对不上维表（示例：{sample}），"
            "请检查清洗后的取值是否都在维表里 —— 装载层不允许静默丢行。"
        )

    columns = [
        "load_batch",
        "source_row_no",
        "respondent_id",
        "gender",
        "major_key",
        "education_key",
        "direction_key",
        "city_key",
        "industry_key",
        "expected_salary",
        "satisfaction",
        "has_internship",
        "salary_is_filled",
        "satisfaction_is_filled",
    ]
    frame = frame.reindex(columns=columns)
    rows = [tuple(None if pd.isna(v) else v for v in record) for record in frame.itertuples(index=False)]
    conn.executemany(_insert_sql(conn, "dwd_survey_clean", columns), rows)
    return len(rows)


def _missing_flag(series: pd.Series) -> pd.Series:
    """原始数据里该列是否缺失（清洗填充后用它标记「这是填充值」）。"""
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.isna().astype(int)


# =============================================================================
# 装载：ADS 结果表
# =============================================================================
RUN_META_COLUMNS = [
    "run_id",
    "load_batch",
    "data_file",
    "data_sha256",
    "rows_raw",
    "rows_clean",
    "rows_invalid_salary",
    "rows_outlier_removed",
    "missing_filled_json",
    "min_group_size",
    "min_recommend_samples",
    "ci_level",
    "alpha",
    "python_version",
    "pandas_version",
    "numpy_version",
    "scipy_version",
    "run_at",
]


def _load_run_meta(
    conn: Any,
    info: DataSourceInfo,
    raw: pd.DataFrame,
    clean: pd.DataFrame,
    cleaning: CleaningReport,
    run_id: str,
    load_batch: str,
) -> int:
    _delete(conn, "ads_run_meta", f"run_id = {_ph(conn, 1)}", (run_id,))
    values: tuple[Any, ...] = (
        run_id,
        load_batch,
        str(info.path),
        hashlib.sha256(Path(info.path).read_bytes()).hexdigest(),
        int(cleaning.original_rows or len(raw)),
        len(clean),
        int(cleaning.invalid_salary_removed),
        int(cleaning.outliers_removed),
        json.dumps(cleaning.missing_filled, ensure_ascii=False),
        int(config.MIN_GROUP_SIZE),
        int(config.MIN_RECOMMEND_SAMPLES),
        float(config.CI_LEVEL),
        float(config.ALPHA),
        platform.python_version(),
        _module_version("pandas"),
        _module_version("numpy"),
        _module_version("scipy"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    conn.execute(_insert_sql(conn, "ads_run_meta", RUN_META_COLUMNS), values)
    return 1


GROUP_STATS_COLUMNS = [
    "run_id",
    "dimension",
    "group_value",
    "sample_size",
    "mean_salary",
    "median_salary",
    "p25",
    "p50",
    "p75",
    "salary_std",
    "salary_ci_low",
    "salary_ci_high",
    "mean_satisfaction",
    "internship_rate",
    "sample_ok",
]


def _load_group_stats(conn: Any, results: dict[str, Any], run_id: str) -> int:
    """写 ads_group_stats —— 数字直接来自 analysis.group_stats 的 DataFrame。"""
    table = results.get("group_stats")
    _delete(conn, "ads_group_stats", f"run_id = {_ph(conn, 1)}", (run_id,))
    if not isinstance(table, pd.DataFrame) or table.empty:
        return 0

    frame = table.assign(run_id=run_id).reindex(columns=GROUP_STATS_COLUMNS)
    rows = [tuple(None if pd.isna(v) else v for v in record) for record in frame.itertuples(index=False)]
    conn.executemany(_insert_sql(conn, "ads_group_stats", GROUP_STATS_COLUMNS), rows)
    return len(rows)


def _load_major_city_matrix(conn: Any, results: dict[str, Any], run_id: str) -> int:
    """写 ads_major_city_matrix：均值为 NaN 的格子按「样本量不足」落库（均值留空）。"""
    matrix = results.get("major_city")
    counts = results.get("major_city_n")
    _delete(conn, "ads_major_city_matrix", f"run_id = {_ph(conn, 1)}", (run_id,))
    if not isinstance(matrix, pd.DataFrame) or matrix.empty:
        return 0

    count_frame = counts if isinstance(counts, pd.DataFrame) else pd.DataFrame()
    rows: list[tuple[Any, ...]] = []
    for major in matrix.index:
        for city in matrix.columns:
            mean = matrix.at[major, city]
            size = count_frame.at[major, city] if (major in count_frame.index and city in count_frame.columns) else 0
            sample_size = 0 if pd.isna(size) else int(size)
            rows.append(
                (
                    run_id,
                    str(major),
                    str(city),
                    sample_size,
                    None if pd.isna(mean) else float(mean),
                    int(pd.isna(mean)),
                )
            )
    conn.executemany(
        _insert_sql(
            conn,
            "ads_major_city_matrix",
            ["run_id", "major", "city", "sample_size", "mean_salary", "insufficient"],
        ),
        rows,
    )
    return len(rows)


COMPARISON_COLUMNS = [
    "run_id",
    "comparison_key",
    "label",
    "group_a",
    "group_b",
    "n_a",
    "n_b",
    "mean_a",
    "mean_b",
    "mean_diff",
    "ci_low",
    "ci_high",
    "p_welch",
    "p_holm",
    "p_mannwhitney",
    "cohens_d",
    "effect_label",
    "sufficient",
    "significant",
    "verdict",
]


def _load_comparisons(conn: Any, results: dict[str, Any], run_id: str) -> int:
    """写 ads_comparisons：判定与 p 值直接用 Comparison 对象的属性，不在 SQL 侧重算。"""
    from src import stats  # 局部导入：只为了 effect_size_label / 数值清洗

    comparisons = results.get("comparisons") or []
    _delete(conn, "ads_comparisons", f"run_id = {_ph(conn, 1)}", (run_id,))
    rows: list[tuple[Any, ...]] = []
    for index, item in enumerate(comparisons, start=1):
        key = item.key or f"cmp_{index:02d}"
        rows.append(
            (
                run_id,
                key,
                item.label,
                item.group_a,
                item.group_b,
                int(item.n_a),
                int(item.n_b),
                _num_or_none(item.mean_a),
                _num_or_none(item.mean_b),
                _num_or_none(item.diff),
                _num_or_none(item.ci_low),
                _num_or_none(item.ci_high),
                _num_or_none(item.p_welch),
                _num_or_none(item.p_holm),
                _num_or_none(item.p_mannwhitney),
                _num_or_none(item.cohens_d),
                stats.effect_size_label(item.cohens_d),
                int(item.sufficient),
                int(item.significant),
                item.verdict,
            )
        )
    conn.executemany(_insert_sql(conn, "ads_comparisons", COMPARISON_COLUMNS), rows)
    return len(rows)


def _num_or_none(value: Any) -> float | None:
    if value is None or not isinstance(value, (int, float)):
        return None
    return None if pd.isna(value) else float(value)


def _load_quality_checks(conn: Any, results: dict[str, Any], run_id: str) -> int:
    """写 ads_quality_checks：来自 quality.audit 的体检项（判定文字原样搬运）。"""
    audit = results.get("quality") or {}
    checks = audit.get("checks") or []
    _delete(conn, "ads_quality_checks", f"run_id = {_ph(conn, 1)}", (run_id,))
    rows = [
        (run_id, index, item.item, item.detail, item.verdict, int(item.flagged))
        for index, item in enumerate(checks, start=1)
    ]
    conn.executemany(
        _insert_sql(
            conn,
            "ads_quality_checks",
            ["run_id", "seq", "item", "detail", "verdict", "flagged"],
        ),
        rows,
    )
    return len(rows)


def _load_weight_sensitivity(conn: Any, results: dict[str, Any], run_id: str) -> int:
    """写 ads_weight_sensitivity：场景、TOP1、秩相关、是否与默认一致。"""
    payload = results.get("recommend_sensitivity") or {}
    rows_source = payload.get("rows") or []
    _delete(conn, "ads_weight_sensitivity", f"run_id = {_ph(conn, 1)}", (run_id,))
    rows: list[tuple[Any, ...]] = []
    for index, row in enumerate(rows_source, start=1):
        spearman = row.get("与默认排序的秩相关")
        same = row.get("TOP1 是否一致")
        if same == "一致":
            top1_same: int | None = 1
        elif same == "不一致":
            top1_same = 0
        else:
            top1_same = None  # 「样本不足，无法评估」这类文字不硬塞成布尔值
        rows.append(
            (
                run_id,
                index,
                str(row.get("权重场景") or ""),
                _text_or_none(row.get("TOP1 城市")),
                _num_or_none(spearman),
                top1_same,
                str(same) if isinstance(same, str) and same not in ("一致", "不一致") else None,
            )
        )
    conn.executemany(
        _insert_sql(
            conn,
            "ads_weight_sensitivity",
            ["run_id", "seq", "scenario", "top1_city", "spearman_vs_default", "top1_same", "note"],
        ),
        rows,
    )
    return len(rows)


# =============================================================================
# 对外接口
# =============================================================================
# =============================================================================
# 岗位市场层（Boss 数据源）：ODS → 维表 → 事实 → 多值桥表
# =============================================================================
#: 岗位扩展字段（与 src/sources/boss.py 的 EXTRA_COLUMNS 同名）
JOB_LAYER_FIELDS = (
    "job_id",
    "job_title",
    "company",
    "industry",
    "finance_stage",
    "company_size",
    "experience_required",
    "experience_rank",
    "company_size_rank",
    "job_type",
    "salary_lower",
    "salary_upper",
    "salary_months",
    "salary_unit",
    "salary_band_ratio",
    "publish_date",
    "city_district",
    "business_district",
    "welfare",
    "skills",
    "jd_text",
    "jd_len",
    "detail_status",
    "source",
    "crawled_at",
)

ODS_JOB_COLUMNS = (
    "run_id",
    "source_row_no",
    "job_id",
    "payload_json",
    "source",
    "crawled_at",
    "load_batch",
)

JOB_POSTING_COLUMNS = (
    "job_id",
    "run_id",
    "job_title",
    "company_key",
    "city_key",
    "industry_key",
    "job_type",
    "experience_rank",
    "company_size_rank",
    "salary_lower",
    "salary_upper",
    "salary_months",
    "salary_unit",
    "salary_mid",
    "salary_band_ratio",
    "publish_date",
    "first_seen_at",
    "last_seen_at",
    "crawled_at",
    "detail_status",
    "jd_len",
)


def has_job_layer(frame: pd.DataFrame) -> bool:
    """这份数据是不是岗位数据源（有 job_id 列即视为岗位数据）。

    问卷数据没有 job_id，所以岗位层对问卷链路完全透明 —— 装载流程不需要
    调用方传开关，也不会因为岗位表为空而报错。
    """
    return "job_id" in frame.columns


def _int_or_none(value: Any) -> int | None:
    number = _num_or_none(value)
    return None if number is None else int(number)


def _rank_or_unknown(value: Any) -> int:
    """序数列是 NOT NULL：缺失时用 9（=不可排序），而不是 0。"""
    number = _num_or_none(value)
    return 9 if number is None else int(number)


def _date_or_none(value: Any) -> str | None:
    text = _text_or_none(value)
    if not text:
        return None
    return text[:10]


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _split_values(value: Any) -> list[str]:
    """「五险一金|双休」→ ['五险一金', '双休']（去空、去重、保序）。"""
    text = _text_or_none(value)
    if not text:
        return []
    parts = [part.strip() for part in text.replace("，", ",").replace("|", ",").replace("、", ",").split(",")]
    result: list[str] = []
    for part in parts:
        if part and part not in result:
            result.append(part)
    return result


def _simple_key_map(conn: Any, table: str, key_column: str, value_column: str) -> dict[str, int]:
    rows = conn.execute(f"SELECT {value_column}, {key_column} FROM {table}").fetchall()
    return {str(value): int(key) for value, key in rows}


def _load_job_layer(
    conn: Any,
    clean: pd.DataFrame,
    key_map: dict[str, dict[str, int]],
    run_id: str,
    load_batch: str,
) -> dict[str, int]:
    """装载岗位市场层（幂等：job_id 为主键，重复装载覆盖）。

    顺序不能换：维表 → 事实表 → 桥表（桥表的外键指向事实表）。
    行业维度沿用 dim_company.industry：岗位本身没有独立行业字段，
    硬造一个 dim_industry 条目会把问卷口径的行业维表搅浑。
    """
    records = clean.to_dict("records")
    written: dict[str, int] = {}

    # ---- 1) ODS：一条岗位一行 JSON（保真，供追溯；不改字段名）----
    ods_rows = [
        (
            run_id,
            position,
            _text_or_none(record.get("job_id")) or "",
            json.dumps(
                {
                    column: _json_value(record.get(column))
                    for column in JOB_LAYER_FIELDS
                    if column in clean.columns
                },
                ensure_ascii=False,
            ),
            _text_or_none(record.get("source")) or "boss",
            _text_or_none(record.get("crawled_at")),
            load_batch,
        )
        for position, record in enumerate(records, start=1)
    ]
    written["ods_job_posting"] = _upsert(
        conn, "ods_job_posting", ODS_JOB_COLUMNS, ("run_id", "source_row_no"), ods_rows
    )

    # ---- 2) 维表：公司 / 技能（技能是多值字段，必须先拆）----
    companies: dict[str, tuple[str | None, str | None, str | None]] = {}
    skills: list[str] = []
    skill_links: list[tuple[str, str]] = []
    welfare_links: list[tuple[str, str]] = []

    for record in records:
        job_id = _text_or_none(record.get("job_id"))
        if not job_id:
            continue
        company = _text_or_none(record.get("company"))
        if company and company not in companies:
            companies[company] = (
                _text_or_none(record.get("industry")),
                _text_or_none(record.get("finance_stage")),
                _text_or_none(record.get("company_size")),
            )
        for skill in _split_values(record.get("skills")):
            skill = skill[:64]
            if skill not in skills:
                skills.append(skill)
            skill_links.append((job_id, skill))
        for welfare in _split_values(record.get("welfare")):
            welfare_links.append((job_id, welfare[:64]))

    if companies:
        written["dim_company"] = _upsert(
            conn,
            "dim_company",
            ("company_name", "industry", "finance_stage", "company_size"),
            ("company_name",),
            [(name, *rest) for name, rest in companies.items()],
        )
    if skills:
        written["dim_skill"] = _upsert(
            conn, "dim_skill", ("skill",), ("skill",), [(skill,) for skill in skills]
        )
    conn.commit()  # 维表先落库，下面查代理键才看得到

    # ---- 3) 事实表：一岗一行 ----
    company_keys = _simple_key_map(conn, "dim_company", "company_key", "company_name")
    skill_keys = _simple_key_map(conn, "dim_skill", "skill_key", "skill")
    city_keys = key_map.get(config.COL_CITY, {})

    posting_rows: list[tuple[Any, ...]] = []
    for record in records:
        job_id = _text_or_none(record.get("job_id"))
        if not job_id:
            continue
        crawled_at = _text_or_none(record.get("crawled_at"))
        posting_rows.append(
            (
                job_id,
                run_id,
                _text_or_none(record.get("job_title")),
                company_keys.get(_text_or_none(record.get("company")) or ""),
                city_keys.get(_text_or_none(record.get(config.COL_CITY)) or ""),
                None,  # 行业走 dim_company.industry
                _text_or_none(record.get("job_type")),
                _rank_or_unknown(record.get("experience_rank")),
                _rank_or_unknown(record.get("company_size_rank")),
                _num_or_none(record.get("salary_lower")),
                _num_or_none(record.get("salary_upper")),
                _int_or_none(record.get("salary_months")),
                _text_or_none(record.get("salary_unit")),
                _num_or_none(record.get(config.COL_SALARY)),
                _num_or_none(record.get("salary_band_ratio")),
                _date_or_none(record.get("publish_date")),
                crawled_at,  # first_seen_at
                crawled_at,  # last_seen_at
                crawled_at,
                _text_or_none(record.get("detail_status")) or "none",
                _int_or_none(record.get("jd_len")),
            )
        )
    written["dwd_job_posting"] = _upsert(
        conn, "dwd_job_posting", JOB_POSTING_COLUMNS, ("job_id",), posting_rows
    )

    # ---- 4) 多值桥表（没有非键列 → 存在即忽略，重复装载不会产生重复行）----
    written["dwd_job_skill"] = _upsert(
        conn,
        "dwd_job_skill",
        ("job_id", "skill_key"),
        ("job_id", "skill_key"),
        [(job_id, skill_keys[skill]) for job_id, skill in skill_links if skill in skill_keys],
    )
    written["dwd_job_welfare"] = _upsert(
        conn,
        "dwd_job_welfare",
        ("job_id", "welfare"),
        ("job_id", "welfare"),
        welfare_links,
    )
    return written


def load_run(
    raw: pd.DataFrame,
    clean: pd.DataFrame,
    results: dict[str, Any],
    info: DataSourceInfo,
    cleaning: CleaningReport,
    *,
    backend: str = "sqlite",
    connection: Any | None = None,
    run_id: str | None = None,
    bootstrap: bool = True,
) -> LoadResult:
    """把一次完整分析（原始数据 + 清洗结果 + 分析结果）装载进 SQL 层。

    参数里的 raw 用于 ODS（原样字符串）、clean 用于 DWD（清洗明细）、
    results 用于 ADS（统计量）；不需要调用方再传任何阈值 —— 阈值从 config 读，
    保证数据库里记录的阈值就是本次运行实际用的阈值。
    """
    if backend not in BACKENDS:
        raise DatabaseError(f"未知后端：{backend}（可选 {BACKENDS}）")

    owns_connection = connection is None
    conn = connection or connect(backend)
    try:
        if bootstrap:
            ensure_schema(conn)

        fingerprint = _data_fingerprint(info.path)
        run_id = run_id or _new_run_id(fingerprint)
        written: dict[str, int] = {}

        # ODS 用字符串版数据；行数与清洗入参必须一致，否则行号会对不上
        raw_text = _read_raw_text(info.path)
        if len(raw_text) != len(raw):
            raise DatabaseError(
                f"原始文件读出 {len(raw_text)} 行，而分析入参是 {len(raw)} 行："
                "行号无法对齐，装载中止。"
            )
        written["ods_employment_survey"] = _load_ods(conn, raw_text, load_batch=fingerprint)

        written.update(_load_dimensions(conn, clean))
        conn.commit()  # 维表先落库，下面的代理键查询才看得到

        key_map = _dimension_key_map(conn)
        written["dwd_survey_clean"] = _load_dwd(conn, raw, clean, fingerprint, key_map)
        if has_job_layer(clean):
            written.update(_load_job_layer(conn, clean, key_map, run_id, fingerprint))
        written["ads_run_meta"] = _load_run_meta(
            conn, info, raw, clean, cleaning, run_id, fingerprint
        )
        written["ads_group_stats"] = _load_group_stats(conn, results, run_id)
        written["ads_major_city_matrix"] = _load_major_city_matrix(conn, results, run_id)
        written["ads_comparisons"] = _load_comparisons(conn, results, run_id)
        written["ads_quality_checks"] = _load_quality_checks(conn, results, run_id)
        written["ads_weight_sensitivity"] = _load_weight_sensitivity(conn, results, run_id)

        checks = run_checks(conn)
        conn.commit()
        return LoadResult(backend=conn.backend, run_id=run_id, rows=written, checks=checks)
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()


def run_checks(conn: Any) -> list[dict[str, Any]]:
    """执行 sql/quality_checks.sql，返回每行检查结果（不懂 SQL 也能读懂）。"""
    cursor = conn.execute(_last_select(CHECKS_SQL))
    names = [description[0] for description in cursor.description]
    return [
        {name: value for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def query(
    sql: str,
    *,
    backend: str = "sqlite",
    connection: Any | None = None,
    params: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    """执行一条查询并返回 dict 列表（文档与测试里用来核对口径）。"""
    owns_connection = connection is None
    conn = connection or connect(backend)
    try:
        cursor = conn.execute(sql, params)
        names = [description[0] for description in cursor.description]
        return [{name: value for name, value in zip(names, row)} for row in cursor.fetchall()]
    finally:
        if owns_connection:
            conn.close()


def run_query_file(name: str, *, backend: str = "sqlite", connection: Any | None = None) -> list[dict[str, Any]]:
    """执行 sql/queries/ 下的一个查询文件（执行前请先装载数据）。"""
    filename = name if name.endswith(".sql") else f"{name}.sql"
    path = QUERY_DIR / filename
    if not path.exists():
        raise DatabaseError(f"找不到查询文件：{path}")
    return query(_last_select(path), backend=backend, connection=connection)