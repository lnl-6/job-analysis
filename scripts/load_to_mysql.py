# -*- coding: utf-8 -*-
"""把一次完整分析装载进 MySQL / SQLite：ODS → DWD → ADS，并跑阻断式门禁。

用法示例：
    # 本地零依赖验证（SQLite 镜像，默认写到 output/employment_analysis.db）
    python scripts/load_to_mysql.py

    # 装进 MySQL（先用 sql/00_schema.sql 建库；CI 里由 service container 起库）
    python scripts/load_to_mysql.py --backend mysql --host 127.0.0.1 --user root \
        --password secret --database employment_analysis

    # 只跑门禁（在已经装载过的库上做体检）
    python scripts/load_to_mysql.py --backend mysql --checks-only

    # 装载后顺手跑一个查询文件（sql/queries/ 下）
    python scripts/load_to_mysql.py --query 02_salary_percentiles_cross_check

退出码：0 = 通过；1 = 门禁有阻断项；2 = 运行出错（缺驱动、连不上、数据读不了等）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd  # noqa: E402

from src import analysis, cleaner, config, db, loader  # noqa: E402


def _console_safe() -> None:
    """中文 Windows 控制台是 GBK：把不可编码字符替换掉，别让打印崩掉整个流程。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def _default_data_path() -> Path:
    """真实数据优先、演示数据兜底（与 main.py 的规则一致）。"""
    if config.REAL_DATA_PATH.exists():
        return config.REAL_DATA_PATH
    return config.DEMO_DATA_PATH


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把分析结果装载进 SQL 层（ODS/DWD/ADS）并执行数据质量门禁",
    )
    parser.add_argument("--data", default=None, help="数据文件路径（默认：真实数据优先，演示数据兜底）")
    parser.add_argument("--backend", choices=db.BACKENDS, default="sqlite", help="后端：mysql 或 sqlite（默认）")
    parser.add_argument("--sqlite-path", default=None, help="SQLite 文件路径（默认 output/employment_analysis.db）")
    parser.add_argument("--host", default="127.0.0.1", help="MySQL 主机")
    parser.add_argument("--port", type=int, default=3306, help="MySQL 端口")
    parser.add_argument("--user", default="root", help="MySQL 用户")
    parser.add_argument("--password", default="", help="MySQL 密码（CI 里由环境变量提供）")
    parser.add_argument("--database", default="employment_analysis", help="MySQL 数据库名")
    parser.add_argument("--top", type=int, default=5, help="专业维度取前 N 名（默认 5）")
    parser.add_argument("--run-id", default=None, help="指定运行编号（重复装载同一批结果时用它验证幂等）")
    parser.add_argument("--checks-only", action="store_true", help="只跑门禁，不装载")
    parser.add_argument("--query", default=None, help="装载后执行 sql/queries/ 下的某个查询文件并打印结果")
    return parser.parse_args(argv)


def _connection(args: argparse.Namespace) -> object:
    if args.backend == "sqlite":
        path = Path(args.sqlite_path) if args.sqlite_path else config.ensure_output_dir() / "employment_analysis.db"
        return db.connect("sqlite", path=path)
    return db.connect(
        "mysql",
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
    )


def _print_checks(checks: list[dict[str, object]]) -> list[dict[str, object]]:
    blocked = [
        check
        for check in checks
        if check.get("severity") == "阻断" and int(check.get("violations") or 0) > 0
    ]
    print(f"门禁：共 {len(checks)} 项检查，阻断级违规 {len(blocked)} 项")
    for check in checks:
        violations = int(check.get("violations") or 0)
        mark = "OK  " if violations == 0 else ("阻断" if check.get("severity") == "阻断" else "提示")
        print(f"  [{mark}] {check['check_name']}: {violations} —— {check['detail']}")
    return blocked


def _print_query(name: str, connection: object) -> None:
    rows = db.run_query_file(name, connection=connection)
    print(f"\n查询 {name} 返回 {len(rows)} 行：")
    print(pd.DataFrame(rows).to_string(index=False) if rows else "  （空结果）")


def main(argv: list[str] | None = None) -> int:
    _console_safe()
    args = parse_args(argv)
    connection = None

    try:
        connection = _connection(args)

        if args.checks_only:
            # 只体检已有的库：**不引导 schema** —— schema 脚本里有 DROP TABLE，
            # 在已装载的库上重跑会把数据清掉。
            blocked = _print_checks(db.run_checks(connection))
            if args.query:
                _print_query(args.query, connection)
            return 1 if blocked else 0

        db.ensure_schema(connection)

        data_path = Path(args.data) if args.data else _default_data_path()
        if not data_path.exists():
            print(f"[错误] 找不到数据文件：{data_path}")
            print("       放一份 CSV 到 data/raw/，或用 --data 指定路径。")
            return 2

        print(f"[1/5] 加载数据：{data_path}")
        df_raw, info = loader.load_data(data_path)

        print("[2/5] 清洗数据")
        df_clean, cleaning = cleaner.clean(df_raw)

        print(f"[3/5] 分析（专业维度取前 {args.top}）")
        results = analysis.run_all(df_clean, top_n=args.top)

        print(f"[4/5] 装载到 {args.backend}")
        outcome = db.load_run(
            df_raw,
            df_clean,
            results,
            info,
            cleaning,
            connection=connection,
            run_id=args.run_id,
            bootstrap=False,  # schema 上面已经引导过了
        )
        for line in outcome.summary_lines():
            print("      " + line)

        print("[5/5] 门禁")
        blocked = _print_checks(outcome.checks)

        if args.query:
            _print_query(args.query, connection)

        return 1 if blocked else 0
    except db.DatabaseError as exc:
        print(f"[错误] {exc}")
        _print_debug_traceback()
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI 要给可读的失败信息，而不是整段堆栈
        print(f"[错误] {type(exc).__name__}: {exc}")
        _print_debug_traceback()
        return 2
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # pragma: no cover - 关闭失败不影响退出码
                pass


def _print_debug_traceback() -> None:
    """JOB_ANALYSIS_DEBUG=1 时打印完整堆栈。

    默认不打印：正常使用只需要一行可读错误；排查（尤其是只能在 CI 上复现的
    数据库方言问题）时，堆栈里的 SQL 语句和参数才是关键信息。
    """
    if os.environ.get("JOB_ANALYSIS_DEBUG") not in {"1", "true", "True", "yes"}:
        return
    import traceback

    print("---- 调试堆栈（JOB_ANALYSIS_DEBUG=1）----")
    traceback.print_exc()


if __name__ == "__main__":
    raise SystemExit(main())