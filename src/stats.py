# -*- coding: utf-8 -*-
"""统计推断层：所有对比都以「测量值 + 不确定性 + 显著性」的形式给出。

本项目的硬规则（写在这里，也写在 README 里）：

1. **报告里出现的每个数字都必须由当次运行算出来。**
   程序内不存放任何预设结论，README 也不复述任何一次运行的结果。
2. **只做描述性测量与区间估计，不做预测。**
   输出的是「本样本里两组的均值差、95% 置信区间、检验 p 值、效应量」，
   不是「谁一定更高」，也不是「未来会怎样」。
3. **样本量不够就不给结论**（门槛见 config.MIN_GROUP_SIZE）。
   1 个样本的均值不是发现，是噪声。

依赖 scipy.stats（requirements.txt 已声明）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src import config

try:
    from scipy import stats as sps
except ImportError as exc:  # pragma: no cover - 只在不满足依赖时触发
    raise ImportError(
        "缺少 scipy：请先执行 pip install -r requirements.txt"
        "（报告里的显著性检验需要它）"
    ) from exc


def _clean(series: pd.Series) -> np.ndarray:
    """转成去掉缺失值的 float 数组。"""
    values = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return values.to_numpy(dtype=float)


def _is_num(value: float) -> bool:
    """不是 NaN 才算有效数字。"""
    return value == value


def effect_size_label(d: float) -> str:
    """按 Cohen 的经验标准给效应量标注大小（只是标注，不是结论）。"""
    if not _is_num(d):
        return "无法计算"
    a = abs(d)
    if a < 0.2:
        return "可忽略"
    if a < 0.5:
        return "小"
    if a < 0.8:
        return "中"
    return "大"


def p_text(p: float) -> str:
    """p 值的展示格式。"""
    if not _is_num(p):
        return "N/A"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


@dataclass(frozen=True)
class Comparison:
    """两个分组的对比结果：所有字段都是算出来的，没有一个是写死的。"""

    label: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    mean_a: float
    mean_b: float
    diff: float
    ci_low: float
    ci_high: float
    p_welch: float
    p_mannwhitney: float
    cohens_d: float
    p_holm: float = float("nan")
    key: str = ""  # 稳定标识：写进 ads_comparisons.comparison_key（报告标题改字不影响它）

    @property
    def p_used(self) -> float:
        """实际用于判定的 p 值：算了 Holm 校正就用校正后的，否则用原始值。"""
        return self.p_holm if _is_num(self.p_holm) else self.p_welch

    @property
    def sufficient(self) -> bool:
        """两组样本量是否都达到最小样本量门槛。"""
        return (
            self.n_a >= config.MIN_GROUP_SIZE
            and self.n_b >= config.MIN_GROUP_SIZE
        )

    @property
    def significant(self) -> bool:
        """达到样本量门槛、且用于判定的 p < ALPHA，才算「差异显著」。"""
        used = self.p_used
        return self.sufficient and _is_num(used) and used < config.ALPHA

    @property
    def verdict(self) -> str:
        """由数值直接得出的判定短语；报告里的结论文字只用它，不另写判断。"""
        if not self.sufficient:
            return f"样本不足（<{config.MIN_GROUP_SIZE}），不给结论"
        if not _is_num(self.p_used):
            return "无法检验（组内没有变异）"
        return "差异显著" if self.significant else "差异不显著"

    def to_row(self) -> dict[str, object]:
        """转成报告表格的一行。"""
        return {
            "对比": self.label,
            "A 组（n）": f"{self.group_a}（{self.n_a}）",
            "B 组（n）": f"{self.group_b}（{self.n_b}）",
            "均值差": round(self.diff, 1),
            "95% 置信区间": f"[{self.ci_low:+.0f}, {self.ci_high:+.0f}]",
            "p（Welch）": p_text(self.p_welch),
            "p（Holm 校正）": p_text(self.p_holm) if _is_num(self.p_holm) else "—",
            "p（Mann-Whitney）": p_text(self.p_mannwhitney),
            "Cohen's d": round(self.cohens_d, 2) if _is_num(self.cohens_d) else "N/A",
            "判定": self.verdict,
        }


def holm_adjust(pvalues: list[float]) -> list[float]:
    """Holm 逐步校正（family-wise error rate）。

    同一次运行里会做多组对比（学历 / 实习 / 城市 / 专业…），组数一多，光靠
    α=0.05 就会稳定地出现假阳性。这里把同一批对比当成一个「检验族」：
    把 m 个 p 值升序排列，第 i 小的乘 (m - i)，再取前缀最大值保证单调。
    无法计算的 p 值（NaN）原样返回。
    """
    adjusted = [float("nan")] * len(pvalues)
    valid = [(i, p) for i, p in enumerate(pvalues) if _is_num(p)]
    if not valid:
        return adjusted

    order = sorted(valid, key=lambda item: item[1])
    total = len(order)
    running = 0.0
    for rank, (index, p) in enumerate(order):
        value = min(1.0, p * (total - rank))
        running = max(running, value)
        adjusted[index] = min(1.0, running)
    return adjusted


def compare(
    a: pd.Series,
    b: pd.Series,
    label: str,
    group_a: str = "A",
    group_b: str = "B",
) -> Comparison:
    """比较两组数值：均值差 + 95% 置信区间 + Welch t 检验 + Mann-Whitney U + Cohen's d。

    Args:
        a: A 组数值序列
        b: B 组数值序列
        label: 人类可读的对比名（例：学历：博士 vs 本科）
        group_a: A 组名称
        group_b: B 组名称

    Returns:
        Comparison（全部由传入数据算出）

    Raises:
        ValueError: 任意一组没有样本。
    """
    x, y = _clean(a), _clean(b)
    if x.size == 0 or y.size == 0:
        raise ValueError(f"「{label}」有一组没有样本，无法比较")

    mean_a, mean_b = float(x.mean()), float(y.mean())
    diff = mean_a - mean_b

    var_a = float(x.var(ddof=1)) if x.size >= 2 else float("nan")
    var_b = float(y.var(ddof=1)) if y.size >= 2 else float("nan")

    # Welch t 检验（不假设两组方差相等）
    p_welch = float("nan")
    if x.size >= 2 and y.size >= 2:
        p_welch = float(sps.ttest_ind(x, y, equal_var=False).pvalue)

    # Mann-Whitney U：薪资右偏，用秩检验做稳健性对照
    try:
        p_mwu = float(sps.mannwhitneyu(x, y, alternative="two-sided").pvalue)
    except ValueError:
        p_mwu = float("nan")

    # Welch–Satterthwaite 自由度下的均值差置信区间
    ci_low, ci_high = diff, diff
    if _is_num(var_a) and _is_num(var_b):
        se_sq = var_a / x.size + var_b / y.size
        if se_sq > 0:
            se = float(np.sqrt(se_sq))
            df = se_sq**2 / (
                (var_a / x.size) ** 2 / (x.size - 1)
                + (var_b / y.size) ** 2 / (y.size - 1)
            )
            t_crit = float(sps.t.ppf(0.5 + config.CI_LEVEL / 2, df))
            ci_low, ci_high = diff - t_crit * se, diff + t_crit * se

    # Cohen's d（合并标准差）
    cohens_d = float("nan")
    if _is_num(var_a) and _is_num(var_b) and (x.size + y.size) > 2:
        pooled = float(
            np.sqrt(
                ((x.size - 1) * var_a + (y.size - 1) * var_b)
                / (x.size + y.size - 2)
            )
        )
        if pooled > 0:
            cohens_d = diff / pooled

    return Comparison(
        label=label,
        group_a=group_a,
        group_b=group_b,
        n_a=int(x.size),
        n_b=int(y.size),
        mean_a=round(mean_a, 2),
        mean_b=round(mean_b, 2),
        diff=diff,
        ci_low=ci_low,
        ci_high=ci_high,
        p_welch=p_welch,
        p_mannwhitney=p_mwu,
        cohens_d=cohens_d,
    )


def describe(series: pd.Series) -> dict[str, float | int]:
    """单组数值的描述性统计 + 均值的 95% 置信区间（全部现场计算）。"""
    v = _clean(series)
    if v.size == 0:
        return {
            "样本量": 0,
            "均值": 0.0,
            "中位数": 0.0,
            "标准差": 0.0,
            "均值置信区间下界": 0.0,
            "均值置信区间上界": 0.0,
        }

    mean = float(v.mean())
    std = float(v.std(ddof=1)) if v.size >= 2 else 0.0
    lo = hi = mean
    if v.size >= 2 and std > 0:
        se = std / float(np.sqrt(v.size))
        t_crit = float(sps.t.ppf(0.5 + config.CI_LEVEL / 2, v.size - 1))
        lo, hi = mean - t_crit * se, mean + t_crit * se

    return {
        "样本量": int(v.size),
        "均值": round(mean, 2),
        "中位数": round(float(np.median(v)), 2),
        "标准差": round(std, 2),
        "均值置信区间下界": round(lo, 2),
        "均值置信区间上界": round(hi, 2),
    }


def quantiles(
    series: pd.Series, levels: tuple[float, ...] = (0.25, 0.5, 0.75)
) -> dict[str, float]:
    """分组分位数（线性插值，与 numpy / pandas 的默认口径一致）。

    单独成函数是为了写清口径来源：SQL 侧的分位数用 nearest-rank 定义
    （见 sql/01_views.sql），两边数值会有小数点级别的差异。
    报告与 BI 的对外数字以这里为准，SQL 侧用于交叉核对。
    """
    v = _clean(series)
    if v.size == 0:
        return {f"p{int(level * 100)}": float("nan") for level in levels}
    return {f"p{int(level * 100)}": round(float(np.quantile(v, level)), 2) for level in levels}


def rank_stability(base: pd.Series, other: pd.Series) -> float:
    """两套排序的 Spearman 秩相关（只比较两者共有的条目）。

    用来回答「权重一改，排序会不会翻」：数值由当次运行算出，
    共同条目少于 2 个时返回 NaN（样本太少，无法评估稳定性）。
    """
    index = base.index.intersection(other.index)
    if len(index) < 2:
        return float("nan")

    left = base.loc[index].to_numpy(dtype=float)
    right = other.loc[index].to_numpy(dtype=float)
    # 任一序列是常量时相关系数没有定义（scipy 会抛 ConstantInputWarning），直接返回 NaN
    if np.unique(left).size < 2 or np.unique(right).size < 2:
        return float("nan")
    return float(sps.spearmanr(left, right).statistic)