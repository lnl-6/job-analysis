# -*- coding: utf-8 -*-
"""数据加载与校验。

数据源：`data/raw/boss_岗位数据集.csv`（本仓库自采的 Boss 岗位数据，
由 `scripts/crawl_boss.py` 抓到原始 JSONL、再由 `src/sources/boss.py` 适配而成）。

仓库不带数据集本身（连样例 JSONL 也不带）。clone 下来后需要自己抓一份：
    python scripts/crawl_boss.py --cookie-file cookie.txt --to-csv   # 抓取 + 适配

需要时也可以用 --data 指定任意同口径 CSV。

加载成功后会返回 source_info，让上层能在报告里如实标注数据来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src import config


class DataLoadError(RuntimeError):
    """数据加载失败。"""


@dataclass(frozen=True)
class DataSourceInfo:
    """描述本次数据来自哪个文件、有多大。"""

    path: Path
    rows: int
    cols: int

    def describe(self) -> str:
        return f"{self.path.name}｜{self.rows} 行 × {self.cols} 列"


def default_data_candidates() -> list[Path]:
    """默认数据源：只认本项目自己抓取的 Boss 岗位数据。"""
    return [config.BOSS_DATA_PATH]


def _candidate_paths(explicit: str | Path | None) -> list[Path]:
    if explicit is not None:
        return [Path(explicit)]
    return default_data_candidates()


def load_data(path: str | Path | None = None) -> tuple[pd.DataFrame, DataSourceInfo]:
    """加载数据并做结构校验。

    Args:
        path: 指定数据文件路径；为 None 时按优先级自动探测。

    Returns:
        (DataFrame, DataSourceInfo)

    Raises:
        DataLoadError: 找不到数据文件，或列结构不完整。
    """
    tried: list[str] = []

    for candidate in _candidate_paths(path):
        if not candidate.exists():
            tried.append(str(candidate))
            continue

        try:
            df = pd.read_csv(candidate, encoding="utf-8-sig")
        except UnicodeDecodeError:
            # 兼容 GBK 编码的旧数据文件
            df = pd.read_csv(candidate, encoding="gbk")
        except Exception as exc:  # noqa: BLE001 - 需要把原始错误暴露给用户
            raise DataLoadError(f"读取 {candidate} 失败：{exc}") from exc

        _validate_columns(df, candidate)

        info = DataSourceInfo(
            path=candidate,
            rows=len(df),
            cols=df.shape[1],
        )
        return df, info

    raise DataLoadError(
        "未找到数据文件。已尝试：\n  - "
        + "\n  - ".join(tried)
        + "\n\n解决方法：\n"
        "  1. 现场抓取并适配：python scripts/crawl_boss.py --cookie-file cookie.txt --to-csv\n"
        "  2. 或用 --data 指定任意同口径 CSV（仓库不携带数据集）"
    )


def _validate_columns(df: pd.DataFrame, path: Path) -> None:
    """校验必需列是否存在，缺失时报出具体列名。"""
    missing = [c for c in config.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataLoadError(
            f"{path} 缺少必需列：{missing}\n"
            f"当前文件实际列名：{df.columns.tolist()}\n"
            f"请参考 data/README.md 的字段说明。"
        )


def load_clean_ready(path: str | Path | None = None) -> tuple[pd.DataFrame, DataSourceInfo]:
    """加载后立即做基础类型转换（薪资/满意度转数值）。"""
    df, info = load_data(path)
    df = df.copy()
    df[config.COL_SALARY] = pd.to_numeric(df[config.COL_SALARY], errors="coerce")
    df[config.COL_SATISFACTION] = pd.to_numeric(df[config.COL_SATISFACTION], errors="coerce")
    return df, info
