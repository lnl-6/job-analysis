# -*- coding: utf-8 -*-
"""分析计算层：只做统计，不画图、不打印。

所有函数返回结构化结果（DataFrame / dict），
由 viz.py 负责可视化、report.py 负责生成结论文字。

这样做的好处：
    - 结论由数据真正算出来，而不是写死在代码里（修正原脚本的硬编码问题）
    - 分析逻辑可单独测试
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from src import config, quality, recommender, stats


def edu_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """学历维度：平均期望薪资 / 人数 / 平均满意度。

    学历按 本科 < 硕士 < 博士 排序（而非字典序）。
    """
    result = (
        df.groupby(config.COL_EDU)
        .agg(
            平均期望薪资=(config.COL_SALARY, "mean"),
            人数=(config.COL_SALARY, "count"),
            平均满意度=(config.COL_SATISFACTION, "mean"),
        )
        .round(2)
    )
    order = ["本科", "硕士", "博士"]
    known = [x for x in order if x in result.index]
    unknown = [x for x in result.index if x not in order]
    return result.loc[known + unknown]


def intern_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """实习经历维度：有 / 无 实习的平均薪资与人数。"""
    return (
        df.groupby(config.COL_INTERN)
        .agg(
            平均期望薪资=(config.COL_SALARY, "mean"),
            人数=(config.COL_SALARY, "count"),
        )
        .round(2)
    )


def major_salary(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """专业维度：按平均期望薪资降序，取前 top_n。"""
    result = (
        df.groupby(config.COL_MAJOR)
        .agg(
            平均期望薪资=(config.COL_SALARY, "mean"),
            人数=(config.COL_SALARY, "count"),
        )
        .round(2)
        .sort_values("平均期望薪资", ascending=False)
    )
    return result.head(top_n)


def direction_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """毕业去向维度：平均薪资 / 人数 / 平均满意度 / 满意度中位数 / 标准差。"""
    result = (
        df.groupby(config.COL_DIRECTION)
        .agg(
            平均期望薪资=(config.COL_SALARY, "mean"),
            人数=(config.COL_SALARY, "count"),
            平均满意度=(config.COL_SATISFACTION, "mean"),
            满意度中位数=(config.COL_SATISFACTION, "median"),
            满意度标准差=(config.COL_SATISFACTION, "std"),
        )
        .round(2)
        .sort_values("平均期望薪资", ascending=False)
    )
    return result


def city_analysis(df: pd.DataFrame, cities: list[str] | None = None) -> pd.DataFrame:
    """城市维度：限定主要城市，按平均期望薪资降序。"""
    cities = cities or config.MAIN_CITIES
    subset = df[df[config.COL_CITY].isin(cities)]
    result = (
        subset.groupby(config.COL_CITY)
        .agg(
            平均期望薪资=(config.COL_SALARY, "mean"),
            人数=(config.COL_SALARY, "count"),
        )
        .round(2)
        .sort_values("平均期望薪资", ascending=False)
    )
    return result


def major_city_cross(
    df: pd.DataFrame,
    top_majors: int = 5,
    cities: list[str] | None = None,
    min_n: int | None = None,
) -> pd.DataFrame:
    """专业 × 城市 交叉分析：平均期望薪资矩阵。

    行 = 专业（按整体平均期望薪资降序，取前 top_majors），列 = 主要城市。
    单元格为「该专业在该城市的平均期望薪资」；**样本量低于 min_n（默认
    config.MIN_GROUP_SIZE）的组合一律置为 NaN**（画图时留空）——
    1~2 个样本的均值不是发现。各组合的原始样本量见 major_city_counts()。
    """
    min_n = config.MIN_GROUP_SIZE if min_n is None else min_n
    cities = cities or config.MAIN_CITIES
    top = major_salary(df, top_n=top_majors).index.tolist()
    if not top:
        return pd.DataFrame()

    subset = df[df[config.COL_MAJOR].isin(top) & df[config.COL_CITY].isin(cities)]
    if subset.empty:
        return pd.DataFrame()

    cross = subset.pivot_table(
        index=config.COL_MAJOR,
        columns=config.COL_CITY,
        values=config.COL_SALARY,
        aggfunc="mean",
    ).round(2)
    counts = subset.pivot_table(
        index=config.COL_MAJOR,
        columns=config.COL_CITY,
        values=config.COL_SALARY,
        aggfunc="count",
    )

    # 保持稳定顺序：专业按薪资降序、城市按 config 中配置的顺序
    cross = cross.reindex(index=top, columns=cities)
    counts = counts.reindex(index=top, columns=cities)

    # 样本量不足的组合置为 NaN（报告与热力图都不会再展示小样本均值）
    return cross.where(counts >= min_n)


def major_city_counts(
    df: pd.DataFrame,
    top_majors: int = 5,
    cities: list[str] | None = None,
) -> pd.DataFrame:
    """专业 × 城市 交叉：每个组合的样本量（报告里如实打印，供读者自行判断）。"""
    cities = cities or config.MAIN_CITIES
    top = major_salary(df, top_n=top_majors).index.tolist()
    if not top:
        return pd.DataFrame()

    subset = df[df[config.COL_MAJOR].isin(top) & df[config.COL_CITY].isin(cities)]
    if subset.empty:
        return pd.DataFrame()

    counts = subset.pivot_table(
        index=config.COL_MAJOR,
        columns=config.COL_CITY,
        values=config.COL_SALARY,
        aggfunc="count",
    )
    return counts.reindex(index=top, columns=cities)


def tier_comparison(df: pd.DataFrame) -> dict[str, float]:
    """一线城市 vs 新一线城市：平均期望薪资对比。"""
    sal = config.COL_SALARY

    tier1 = df[df[config.COL_CITY].isin(config.TIER1_CITIES)][sal]
    new_tier1 = df[df[config.COL_CITY].isin(config.NEW_TIER1_CITIES)][sal]

    return {
        "一线平均": round(float(tier1.mean()), 2) if not tier1.empty else 0.0,
        "新一线平均": round(float(new_tier1.mean()), 2) if not new_tier1.empty else 0.0,
        "一线样本数": int(tier1.count()),
        "新一线样本数": int(new_tier1.count()),
    }


def overall_summary(df: pd.DataFrame) -> dict[str, float | int]:
    """整体概况：样本量、薪资均值/中位数、满意度均值。"""
    desc = stats.describe(df[config.COL_SALARY])
    return {
        "样本量": len(df),
        "平均期望薪资": desc["均值"],
        "期望薪资中位数": desc["中位数"],
        "薪资均值置信区间下界": desc["均值置信区间下界"],
        "薪资均值置信区间上界": desc["均值置信区间上界"],
        "平均满意度": round(float(df[config.COL_SATISFACTION].mean()), 2),
        "有实习占比": round(
            float((df[config.COL_INTERN] == "是").mean() * 100), 1
        ),
    }


def _salary_of(df: pd.DataFrame, column: str, value: str) -> pd.Series:
    """取某个分类取值对应的薪资列（样本里没有该取值时返回空 Series）。"""
    return df.loc[df[column] == value, config.COL_SALARY]


def comparisons(df: pd.DataFrame) -> list[stats.Comparison]:
    """把值得比较的两组配对算出来：均值差 + 置信区间 + 检验 + 效应量。

    全部由当次数据算出；样本里不存在某个取值时直接跳过该对比；
    「专业两端」也由数据里的薪资排名决定，不预先指定谁跟谁比。
    多组对比的 p 值统一做 Holm 校正（同一检验族内控制假阳性）。
    """
    out: list[stats.Comparison] = []
    sal = config.COL_SALARY

    def add(
        label: str,
        name_a: str,
        name_b: str,
        a: pd.Series,
        b: pd.Series,
        key: str,
    ) -> None:
        if a.empty or b.empty:
            return
        # key 是给 SQL 结果表用的稳定标识（ads_comparisons.comparison_key）：
        # 报告里的中文标题可以随便改，数据库里的键不会跟着变。
        out.append(
            replace(
                stats.compare(a, b, label=label, group_a=name_a, group_b=name_b),
                key=key,
            )
        )

    add(
        "学历：博士 vs 本科",
        "博士",
        "本科",
        _salary_of(df, config.COL_EDU, "博士"),
        _salary_of(df, config.COL_EDU, "本科"),
        "edu_phd_vs_bachelor",
    )
    add(
        "学历：硕士 vs 本科",
        "硕士",
        "本科",
        _salary_of(df, config.COL_EDU, "硕士"),
        _salary_of(df, config.COL_EDU, "本科"),
        "edu_master_vs_bachelor",
    )
    add(
        "实习：有 vs 无",
        "有实习",
        "无实习",
        _salary_of(df, config.COL_INTERN, "是"),
        _salary_of(df, config.COL_INTERN, "否"),
        "intern_yes_vs_no",
    )
    add(
        "城市：一线 vs 新一线",
        "一线",
        "新一线",
        df.loc[df[config.COL_CITY].isin(config.TIER1_CITIES), sal],
        df.loc[df[config.COL_CITY].isin(config.NEW_TIER1_CITIES), sal],
        "city_tier1_vs_new_tier1",
    )
    add(
        "去向：就业 vs 待业",
        "就业",
        "待业",
        _salary_of(df, config.COL_DIRECTION, "就业"),
        _salary_of(df, config.COL_DIRECTION, "待业"),
        "direction_employed_vs_unemployed",
    )

    # 专业两端：先看数据里谁高谁低，再比较 —— 顺序由数据决定
    if not df.empty:
        by_major = (
            df.groupby(config.COL_MAJOR)[sal]
            .agg(["mean", "count"])
            .dropna(subset=["mean"])
            .sort_values("mean", ascending=False)
        )
        if len(by_major) >= 2:
            high, low = by_major.index[0], by_major.index[-1]
            if high != low:
                add(
                    f"专业：{high} vs {low}",
                    str(high),
                    str(low),
                    _salary_of(df, config.COL_MAJOR, high),
                    _salary_of(df, config.COL_MAJOR, low),
                    "major_top_vs_bottom",
                )

    # 同一次运行做了多组对比 → 用 Holm 法校正 p 值（控制整体假阳性）。
    # 判定与报告都以校正后的 p 为准；只有一组对比时，校正 p 等于原始 p。
    corrected = stats.holm_adjust([item.p_welch for item in out])
    return [replace(item, p_holm=p) for item, p in zip(out, corrected)]


def recommend_sensitivity(df: pd.DataFrame) -> dict[str, object]:
    """换几组权重重算城市排序，量化「排序对权重有多敏感」。

    查询条件由数据自己选：样本量最大的专业 + 出现最多的学历与实习取值；
    **不加薪资区间** —— 真实数据上「薪资 ±1000」会把样本筛到只剩一两个城市，
    秩相关无从计算，这个问题就退化成「没得比」。全部现算，不预设。

    Returns:
        {"query": {条件}, "rows": [每个权重场景一行的对照结果]}
    """
    if df.empty:
        return {"query": {}, "rows": []}

    major = str(df[config.COL_MAJOR].value_counts().idxmax())
    edu = str(df[config.COL_EDU].value_counts().idxmax())
    intern = str(df[config.COL_INTERN].value_counts().idxmax())

    rows = recommender.weight_sensitivity(df, major=major, edu=edu, intern=intern)
    return {
        "query": {"专业": major, "学历": edu, "实习": intern, "期望薪资": "不限"},
        "rows": rows,
    }


def group_stats(df: pd.DataFrame) -> pd.DataFrame:
    """把各维度的分组统计算成一张长表（写进 SQL 的 ads_group_stats）。

    列全部现算，来源写清楚：
      * 样本量 / 均值 / 中位数 / 标准差 / 均值置信区间 → stats.describe
      * 分位数 → stats.quantiles（线性插值口径，与 SQL 侧 nearest-rank 并列核对）
      * 满意度均值、实习率 → 直接聚合
      * 样本是否达标 → config.MIN_GROUP_SIZE

    放在分析层而不是装载层的原因：统计量只算一次。装载层只负责搬运，
    否则报告、数据库、BI 三处会各自算一份，迟早对不上。
    """
    dimensions = {
        "education": (config.COL_EDU, ()),
        "major": (config.COL_MAJOR, ()),
        "city": (config.COL_CITY, config.CITY_UNSPECIFIED),
        "direction": (config.COL_DIRECTION, ()),
        "industry": (config.COL_INDUSTRY, config.INDUSTRY_UNSPECIFIED),
    }

    rows: list[dict[str, object]] = []
    for dimension, (column, excluded) in dimensions.items():
        if column not in df.columns:
            continue
        values = df[column].astype(str).str.strip()
        keep = ~values.isin(excluded) if excluded else pd.Series(True, index=df.index)
        work = df.loc[keep].assign(_group_value=values[keep])

        for value, group in work.groupby("_group_value", sort=True):
            desc = stats.describe(group[config.COL_SALARY])
            quantile = stats.quantiles(group[config.COL_SALARY])
            size = int(len(group))
            rows.append(
                {
                    "dimension": dimension,
                    "group_value": str(value),
                    "sample_size": size,
                    "mean_salary": desc["均值"],
                    "median_salary": desc["中位数"],
                    "p25": quantile["p25"],
                    "p50": quantile["p50"],
                    "p75": quantile["p75"],
                    "salary_std": desc["标准差"],
                    "salary_ci_low": desc["均值置信区间下界"],
                    "salary_ci_high": desc["均值置信区间上界"],
                    "mean_satisfaction": round(
                        float(group[config.COL_SATISFACTION].mean()), 2
                    ),
                    "internship_rate": round(
                        float((group[config.COL_INTERN] == "是").mean() * 100), 2
                    ),
                    "sample_ok": int(size >= config.MIN_GROUP_SIZE),
                }
            )
    return pd.DataFrame(rows)


def run_all(df: pd.DataFrame, top_n: int = 5) -> dict[str, object]:
    """一次性跑完全部分析，返回结果字典。

    Key 与 viz / report 模块约定好的名字对应。

    Args:
        df: 清洗后的数据
        top_n: 专业维度取前 N 名；「专业 × 城市」交叉分析同步使用该值，
               这样报告里的表格行数与 main.py --top 参数始终一致。

    注：本项目不做性别维度分析。
    """
    return {
        "ov": overall_summary(df),
        "edu": edu_analysis(df),
        "intern": intern_analysis(df),
        "major_top": major_salary(df, top_n=top_n),
        "direction": direction_analysis(df),
        "city": city_analysis(df),
        "major_city": major_city_cross(df, top_majors=top_n),
        "major_city_n": major_city_counts(df, top_majors=top_n),
        "tier": tier_comparison(df),
        # 对比检验与推荐权重敏感性也在这一层一次算完，报告层只负责呈现
        "comparisons": comparisons(df),
        "recommend_sensitivity": recommend_sensitivity(df),
        # 数据质量体检：报告在给结论之前，先说清这批数据哪些地方区分不出来
        "quality": quality.audit(df),
        # 各维度合并成一张长表：写进 SQL 的 ads_group_stats 供 BI 切片（报告仍用上面各维度的表）
        "group_stats": group_stats(df),
    }
