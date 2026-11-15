# -*- coding: utf-8 -*-
"""数据清洗：缺失值填充 + IQR 异常值剔除。

设计原则：
    - 每一步都返回「做了什么」的审计记录，方便写进报告、也方便自己复盘
    - 关键不变量用 assert 校验（P1 要求：清洗逻辑必须有验证）

填充策略（沿用原脚本思路，并说明理由）：
    - 是否有实习经历：分类变量 → 众数（最常见取值）
    - 期望薪资：连续变量 → 中位数（比均值抗异常值）
    - 满意度评分：连续变量 → 中位数

    ⚠️ 本项目不做性别维度分析，因此不处理「性别」列（有无该列均可）。

无效值策略：
    - 「期望薪资 ≤ 0」的行视为无效记录，在填充与 IQR 之前直接剔除：
      缺失值可以填，但一条 -2100 元的记录不是缺失，是无效数据。

异常值策略：
    - 对「期望薪资」用 IQR 法（Q1-1.5*IQR, Q3+1.5*IQR）剔除，
      因为薪资分布右偏严重，均值/标准差法会把正常高薪当成异常。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src import config


@dataclass
class CleaningReport:
    """清洗过程的审计记录。"""

    original_rows: int = 0
    invalid_salary_removed: int = 0
    rows_after_missing_fill: int = 0
    rows_after_outlier_removal: int = 0
    missing_filled: dict[str, int] = field(default_factory=dict)
    outliers_removed: int = 0
    iqr_bounds: tuple[float, float] | None = None

    @property
    def final_rows(self) -> int:
        return self.rows_after_outlier_removal or self.rows_after_missing_fill

    @property
    def removed_rows(self) -> int:
        return self.original_rows - self.final_rows

    def summary_lines(self) -> list[str]:
        lines = [
            f"原数据行数：{self.original_rows}",
            f"无效薪资行剔除：{self.invalid_salary_removed} 行"
            "（期望薪资 ≤ 0，不是有效的薪资期望）",
            f"缺失值填充：{self.missing_filled or '无'}",
            f"异常值剔除：{self.outliers_removed} 行"
            + (f"（薪资合理区间 {self.iqr_bounds[0]:.0f} ~ {self.iqr_bounds[1]:.0f} 元）"
               if self.iqr_bounds else ""),
            f"清洗后行数：{self.final_rows}",
            f"合计移除：{self.removed_rows} 行 "
            f"({self.removed_rows / max(self.original_rows, 1) * 100:.1f}%)",
        ]
        return lines


def fill_missing(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """填充缺失值，返回 (新df, 各列填充数量)。"""
    out = df.copy()
    filled: dict[str, int] = {}

    # 分类变量：众数
    for col in (config.COL_INTERN,):
        n_missing = int(out[col].isna().sum())
        if n_missing:
            mode_val = out[col].mode(dropna=True)
            if not mode_val.empty:
                out[col] = out[col].fillna(mode_val.iloc[0])
                filled[col] = n_missing

    # 连续变量：中位数（抗异常值）
    for col in (config.COL_SALARY, config.COL_SATISFACTION):
        n_missing = int(out[col].isna().sum())
        if n_missing:
            median_val = out[col].median()
            out[col] = out[col].fillna(median_val)
            filled[col] = n_missing

    # ---- 不变量校验 ----
    assert len(out) == len(df), "填充缺失值不应改变行数"
    assert out[config.COL_SALARY].isna().sum() == 0, "薪资列填充后不应再有缺失"
    assert out[config.COL_INTERN].isna().sum() == 0, "实习经历列填充后不应再有缺失"

    return out, filled


def drop_invalid_salary(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """剔除「期望薪资 ≤ 0」的行，返回 (新df, 剔除行数)。

    为什么单独一步：缺失值可以用中位数填，但「-2100 元」不是缺失，
    它是无效记录。不先剔除，它就会污染中位数、也会被当成合法观测值。
    注意：真正的缺失值（NaN）不在这里剔除，交给 fill_missing()。
    """
    numeric = pd.to_numeric(df[config.COL_SALARY], errors="coerce")
    invalid = numeric.notna() & (numeric <= 0)
    out = df.loc[~invalid].copy()
    removed = int(invalid.sum())

    # ---- 不变量校验 ----
    assert len(out) + removed == len(df), "剔除无效薪资后行数对不上"
    if not out.empty:
        remaining = pd.to_numeric(out[config.COL_SALARY], errors="coerce")
        assert not (remaining.notna() & (remaining <= 0)).any(), "剔除后仍有非正薪资"

    return out, removed


def remove_salary_outliers(df: pd.DataFrame) -> tuple[pd.DataFrame, int, tuple[float, float]]:
    """用 IQR 法剔除「期望薪资」异常值，返回 (新df, 剔除行数, 区间)。"""
    q1 = float(df[config.COL_SALARY].quantile(0.25))
    q3 = float(df[config.COL_SALARY].quantile(0.75))
    iqr = q3 - q1
    lower = q1 - config.IQR_FACTOR * iqr
    upper = q3 + config.IQR_FACTOR * iqr

    mask = (df[config.COL_SALARY] >= lower) & (df[config.COL_SALARY] <= upper)
    out = df.loc[mask].copy()
    removed = len(df) - len(out)

    # ---- 不变量校验 ----
    assert len(out) <= len(df), "剔除异常值后行数不应变多"
    assert removed >= 0, "剔除行数不应为负"
    if not out.empty:
        assert out[config.COL_SALARY].between(lower, upper).all(), "剔除后仍有越界值"

    return out, removed, (lower, upper)


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, CleaningReport]:
    """完整清洗流程：剔除无效薪资 → 填充缺失值 → IQR 剔除异常值。"""
    report = CleaningReport(original_rows=len(df))

    df_valid, invalid_removed = drop_invalid_salary(df)
    report.invalid_salary_removed = invalid_removed

    df_filled, filled = fill_missing(df_valid)
    report.missing_filled = filled
    report.rows_after_missing_fill = len(df_filled)

    df_clean, removed, bounds = remove_salary_outliers(df_filled)
    report.outliers_removed = removed
    report.iqr_bounds = bounds
    report.rows_after_outlier_removal = len(df_clean)

    # ---- 最终不变量校验 ----
    assert len(df_clean) > 0, "清洗后数据不能为空"
    assert df_clean[config.COL_SALARY].isna().sum() == 0, "清洗后薪资不应有缺失"

    return df_clean, report
