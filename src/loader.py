# -*- coding: utf-8 -*-
"""数据加载与校验。

加载优先级：
    1. data/raw/大学生就业数据.csv   （真实数据）
    2. data/raw/demo_大学生就业数据.csv（演示数据，兜底）

无论谁被加载，都会返回 source_info，让上层能在报告里如实标注数据来源。
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
    """描述本次数据从哪来、是不是合成数据。"""

    path: Path
    rows: int
    cols: int
    is_demo: bool

    @property
    def label(self) -> str:
        return "合成演示数据（非真实）" if self.is_demo else "真实数据"

    def describe(self) -> str:
        return (
            f"{self.label}｜{self.path.name}｜"
            f"{self.rows} 行 × {self.cols} 列"
        )


def _candidate_paths(explicit: str | Path | None) -> list[Path]:
    if explicit is not None:
        return [Path(explicit)]
    return [config.REAL_DATA_PATH, config.DEMO_DATA_PATH]


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
            is_demo=candidate.name.startswith("demo_"),
        )
        return df, info

    raise DataLoadError(
        "未找到数据文件。已尝试：\n  - "
        + "\n  - ".join(tried)
        + "\n\n解决方法（二选一）：\n"
        "  1. 把真实数据放到 data/raw/大学生就业数据.csv\n"
        "  2. 运行 python scripts/generate_demo_data.py 生成演示数据"
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
