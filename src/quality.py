# -*- coding: utf-8 -*-
"""数据质量体检：在给结论之前，先如实说出这批数据「哪些地方不可信」。

设计原则：
    - 只报「能从数据里算出来的现象」：不解释原因、不预测、不猜数据来源
    - 每个判定都有明确阈值，阈值集中在本文件顶部，报告里会照实打印
    - 用单因素 ANOVA 的组间解释力 eta² 回答「这个维度到底有没有区分度」：
      eta² = 组间平方和 / 总平方和；0 = 该维度完全不能区分，1 = 完全区分
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as sps

from src import config

# ---------------- 判定阈值 ----------------
ETA2_WEAK = 0.01          # 组间解释力低于 1%：该维度几乎不解释任何差异
MISSING_WARN = 10.0       # 单列缺失率超过 10%：需要留意
DOMINANT_SHARE = 90.0     # 单一取值占比超过 90%：分布过度集中
HIGH_VALUE_PCT = 20.0     # 高于 2×中位数的样本占比超过 20%：分布不像单峰
SKEW_WARN = 1.0           # 偏度绝对值超过 1：分布明显不对称
FLAT_STD = 0.05           # 标准差低于 0.05：几乎是常量列

# 需要体检的分类维度 / 指标
DIMENSIONS = [
    config.COL_EDU,
    config.COL_MAJOR,
    config.COL_INTERN,
    config.COL_DIRECTION,
    config.COL_CITY,
]
METRICS = [config.COL_SALARY, config.COL_SATISFACTION]


@dataclass
class QualityCheck:
    """一条体检结论：检查项 + 本次实测 + 判定。"""

    item: str
    detail: str
    verdict: str

    @property
    def flagged(self) -> bool:
        """判定里带「提示」的项：需要在报告里被点出来。"""
        return self.verdict.startswith("提示")

    def to_row(self) -> dict[str, str]:
        return {"检查项": self.item, "本次实测": self.detail, "判定": self.verdict}


def missing_rates(df: pd.DataFrame) -> dict[str, float]:
    """每列的缺失率（%）。"""
    if df.empty:
        return {}
    return {
        str(col): round(float(df[col].isna().mean() * 100), 2) for col in df.columns
    }


def count_duplicate_rows(df: pd.DataFrame) -> int:
    """完全重复的行数（同一条记录被录入多次）。"""
    return int(df.duplicated().sum())


def _pvalue(result: object) -> float:
    """兼容不同 scipy 版本的显著性结果对象。"""
    if hasattr(result, "pvalue"):
        return float(result.pvalue)  # type: ignore[attr-defined]
    return float(result[1])  # type: ignore[index]


def group_effect(df: pd.DataFrame, dimension: str, metric: str) -> QualityCheck:
    """这个维度对指标有多少解释力（单因素 ANOVA + eta²）。"""
    groups = [
        group[metric].dropna().to_numpy(dtype=float)
        for _, group in df.groupby(dimension, dropna=False)
    ]
    groups = [g for g in groups if g.size >= 2]
    if len(groups) < 2:
        return QualityCheck(f"{dimension} → {metric}", "有效分组少于 2 个", "无法评估")

    values = np.concatenate(groups)
    grand = float(values.mean())
    ss_between = float(sum(g.size * (g.mean() - grand) ** 2 for g in groups))
    ss_total = float(((values - grand) ** 2).sum())
    eta2 = ss_between / ss_total if ss_total > 0 else 0.0
    p = _pvalue(sps.f_oneway(*groups))

    detail = f"{len(groups)} 组｜ANOVA p={p:.3g}｜eta²={eta2:.3f}（解释力 {eta2 * 100:.1f}%）"
    if eta2 < ETA2_WEAK:
        verdict = "提示：该维度几乎不解释差异"
    elif p < config.ALPHA:
        verdict = "有区分度"
    else:
        verdict = "不显著"
    return QualityCheck(f"{dimension} → {metric}", detail, verdict)


def categorical_dominance(df: pd.DataFrame, dimension: str) -> QualityCheck:
    """某个分类维度里单一取值的最大占比。"""
    values = df[dimension].astype(str).value_counts(normalize=True)
    if values.empty:
        return QualityCheck(f"{dimension} 取值集中度", "无有效取值", "无法评估")
    top_share = float(values.iloc[0] * 100)
    detail = f"取值 {len(values)} 个｜最大占比 {top_share:.1f}%（{values.index[0]}）"
    verdict = "提示：分布过度集中" if top_share >= DOMINANT_SHARE else "分布正常"
    return QualityCheck(f"{dimension} 取值集中度", detail, verdict)


def salary_shape(df: pd.DataFrame) -> list[QualityCheck]:
    """期望薪资的分布形态：偏度/峰度、高值占比。"""
    series = pd.to_numeric(df[config.COL_SALARY], errors="coerce").dropna()
    if series.empty:
        return [QualityCheck("期望薪资分布形态", "无有效数据", "无法评估")]

    median = float(series.median())
    skew = float(series.skew())
    kurt = float(series.kurt())
    ratio = float(series.mean() / median) if median else float("nan")

    shape = QualityCheck(
        "期望薪资分布形态",
        f"偏度 {skew:.2f}｜峰度 {kurt:.2f}｜均值/中位数 {ratio:.2f}",
        "右偏明显" if abs(skew) > SKEW_WARN else "近似对称",
    )

    threshold = 2 * median
    share = float((series > threshold).mean() * 100) if threshold else 0.0
    high = QualityCheck(
        "期望薪资高值占比",
        f"{share:.1f}% 的样本 > 2×中位数（{threshold:.0f} 元）",
        "提示：可能不止一个群体（分布不像单峰）"
        if share >= HIGH_VALUE_PCT
        else "无明显聚集",
    )
    return [shape, high]


def satisfaction_profile(df: pd.DataFrame) -> QualityCheck:
    """满意度取值分布：最常见取值、占比、标准差。"""
    series = pd.to_numeric(df[config.COL_SATISFACTION], errors="coerce").dropna()
    if series.empty:
        return QualityCheck("满意度取值分布", "无有效数据", "无法评估")

    share = series.round(1).value_counts(normalize=True)
    top_share = float(share.iloc[0] * 100)
    std = float(series.std(ddof=1)) if series.size >= 2 else 0.0
    detail = (
        f"取值 {series.nunique()} 个｜最常见 {share.index[0]}"
        f"（占 {top_share:.1f}%）｜标准差 {std:.2f}"
    )
    if std < FLAT_STD:
        verdict = "提示：几乎是常量列，不携带信息"
    elif top_share >= 60:
        verdict = "提示：高度集中"
    else:
        verdict = "分布正常"
    return QualityCheck("满意度取值分布", detail, verdict)


def audit(df: pd.DataFrame) -> dict[str, object]:
    """跑完全部体检项（全部现算，不含任何预设判断）。

    Returns:
        {"rows": 行数, "missing": {列: 缺失率%}, "duplicates": 重复行数,
         "checks": [QualityCheck, ...]}
    """
    checks: list[QualityCheck] = []

    duplicates = count_duplicate_rows(df)
    checks.append(
        QualityCheck(
            "完全重复的行",
            f"{duplicates} 行（占 {duplicates / max(len(df), 1) * 100:.2f}%）",
            "提示：存在重复记录" if duplicates else "无重复",
        )
    )

    rates = missing_rates(df)
    if rates:
        worst_col = max(rates, key=lambda col: rates[col])
        checks.append(
            QualityCheck(
                "缺失率最高的列",
                f"{worst_col}：{rates[worst_col]:.2f}%"
                f"（{int(df[worst_col].isna().sum())} 条）",
                "提示：缺失率偏高"
                if rates[worst_col] > MISSING_WARN
                else "缺失可接受",
            )
        )

    for dimension in DIMENSIONS:
        if dimension in df.columns:
            checks.append(categorical_dominance(df, dimension))

    for dimension in DIMENSIONS:
        for metric in METRICS:
            if dimension in df.columns and metric in df.columns:
                checks.append(group_effect(df, dimension, metric))

    if config.COL_SALARY in df.columns:
        checks.extend(salary_shape(df))
    if config.COL_SATISFACTION in df.columns:
        checks.append(satisfaction_profile(df))

    return {
        "rows": int(len(df)),
        "missing": rates,
        "duplicates": duplicates,
        "checks": checks,
    }


def flagged_items(result: dict[str, object]) -> list[str]:
    """所有判定带「提示」的检查项名（供结论段落引用）。"""
    checks = result.get("checks")
    if not isinstance(checks, list):
        return []
    return [c.item for c in checks if isinstance(c, QualityCheck) and c.flagged]