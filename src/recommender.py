# -*- coding: utf-8 -*-
"""城市排序（描述性）：多条件筛选 + 综合适配得分排序。

方法学修正（原脚本的问题）：
    原公式：得分 = 人数*0.5 + 满意度*0.3 + 薪资/1000*0.2
    问题：三个量纲完全不同（人数 0-100+，满意度 0-5，薪资/1000 约 5-20），
          直接加权会让「人数」压倒性主导，0.5/0.3/0.2 的权重形同虚设。
    修正：先把每个指标做 min-max 归一化到 [0, 1]，再加权求和。

本模块的边界（写清楚，避免被误读）：
    - 输出是**对已收集样本的描述性排序**：把同条件下各城市的
      「就业人数 / 平均满意度 / 平均期望薪资」三个实测指标归一化后加权，
      回答的是「这批样本里哪个城市的三个指标综合更高」。
    - **不做任何预测**：不预测个体结果、不预测未来薪资；
      得分是相对当前筛选子集的排序分，换一组筛选条件分数就会变。
    - 样本量低于 config.MIN_RECOMMEND_SAMPLES 的城市不参与排序；
      低于 config.MIN_GROUP_SIZE 的会在结果里标注「样本偏少」。
"""

from __future__ import annotations

import pandas as pd

from src import config, stats

# 默认权重：就业机会 50% / 满意度 30% / 薪资 20%
DEFAULT_WEIGHTS = {"就业机会": 0.5, "满意度": 0.3, "薪资": 0.2}

# 筛选时允许的薪资浮动区间（元）
DEFAULT_SALARY_TOLERANCE = 1000

# 权重敏感性对照：换几组权重重算一遍，看排序会不会翻（全部现算，不预置答案）
DEFAULT_SCENARIOS: dict[str, dict[str, float]] = {
    "默认 0.5 / 0.3 / 0.2": {"就业机会": 0.5, "满意度": 0.3, "薪资": 0.2},
    "只看就业机会": {"就业机会": 1.0, "满意度": 0.0, "薪资": 0.0},
    "只看满意度": {"就业机会": 0.0, "满意度": 1.0, "薪资": 0.0},
    "只看薪资": {"就业机会": 0.0, "满意度": 0.0, "薪资": 1.0},
    "三者均等": {"就业机会": 1 / 3, "满意度": 1 / 3, "薪资": 1 / 3},
    "与默认相反 0.2 / 0.3 / 0.5": {"就业机会": 0.2, "满意度": 0.3, "薪资": 0.5},
}


def _min_max_normalize(series: pd.Series) -> pd.Series:
    """min-max 归一化到 [0, 1]；常量序列返回全 1（避免除零）。"""
    s = series.astype(float)
    lo, hi = s.min(), s.max()
    if hi - lo == 0:
        return pd.Series(1.0, index=s.index)
    return (s - lo) / (hi - lo)


def _filter(
    df: pd.DataFrame,
    major: str,
    edu: str,
    intern: str,
    salary: int | None,
    tolerance: int,
) -> pd.DataFrame:
    """多条件筛选。返回可能为空的 DataFrame。

    salary=None 表示「不限期望薪资」（权重敏感性分析用：只关心权重变化本身，
    不再用薪资区间筛样本，否则真实数据上选不出足够多的城市做排序比较）。

    另外：城市列里的「无 / 未填」等占位值**不是城市**，一律排除 ——
    否则「无」会被当成一个城市参与排序（真实数据上确实发生了，还排到第一）。
    """
    sal = config.COL_SALARY
    mask = df[config.COL_MAJOR].astype(str).str.contains(major, case=False, na=False)
    out = df[mask]

    if edu:
        out = out[out[config.COL_EDU] == edu]
    if intern:
        out = out[out[config.COL_INTERN] == intern]

    if salary is not None:
        out = out[out[sal].between(salary - tolerance, salary + tolerance)]

    city = out[config.COL_CITY].astype(str).str.strip()
    return out[~city.isin(config.CITY_UNSPECIFIED)]


def _normalized_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    """把权重归一化到和为 1，供打分与文案共用同一份权重。

    Raises:
        ValueError: 权重之和不为正数（否则会静默输出一堆 0 分）。
    """
    merged = dict(weights or DEFAULT_WEIGHTS)
    total = sum(merged.values())
    if total <= 0:
        raise ValueError("权重之和必须大于 0")
    return {k: v / total for k, v in merged.items()}


def _score(
    df: pd.DataFrame,
    major: str,
    edu: str,
    intern: str,
    salary: int | None,
    tolerance: int,
    weights: dict[str, float] | None,
    min_group: int,
) -> pd.DataFrame:
    """算出全部候选城市的得分表（不截断），recommend 与敏感性分析共用。"""
    used = _normalized_weights(weights)

    subset = _filter(df, major, edu, intern, salary, tolerance)
    if subset.empty:
        return pd.DataFrame()

    agg = (
        subset.groupby(config.COL_CITY)
        .agg(
            就业人数=(config.COL_SALARY, "count"),
            平均满意度=(config.COL_SATISFACTION, "mean"),
            平均期望薪资=(config.COL_SALARY, "mean"),
        )
        .round(2)
    )

    # 样本量门槛：低于 min_group 的城市不参与排序（小样本均值不稳定）
    enough = agg[agg["就业人数"] >= min_group]
    if enough.empty:
        return pd.DataFrame()

    # 三个指标归一化后加权（min-max 是本次方法学修正的核心）
    n_opp = _min_max_normalize(enough["就业人数"])
    n_sat = _min_max_normalize(enough["平均满意度"])
    n_sal = _min_max_normalize(enough["平均期望薪资"])

    enough = enough.copy()
    enough["样本是否充足"] = enough["就业人数"] >= config.MIN_GROUP_SIZE
    enough["综合适配得分"] = (
        n_opp * used["就业机会"] + n_sat * used["满意度"] + n_sal * used["薪资"]
    ).round(4)

    result = enough.sort_values("综合适配得分", ascending=False)
    result.attrs["matched_rows"] = int(len(subset))
    result.attrs["min_group"] = int(min_group)
    result.attrs["excluded_cities"] = [
        str(c) for c in agg.index if c not in enough.index
    ]
    return result


def recommend(
    df: pd.DataFrame,
    major: str,
    edu: str = "",
    intern: str = "",
    salary: int = 8000,
    top_n: int = 5,
    tolerance: int = DEFAULT_SALARY_TOLERANCE,
    weights: dict[str, float] | None = None,
    min_group: int | None = None,
) -> pd.DataFrame:
    """按条件给出适配城市的**描述性排序**（不是预测）。

    Args:
        df: 已清洗的数据
        major: 专业关键词（支持模糊匹配）
        edu: 学历，空字符串表示不限
        intern: 是否有实习经历（"是"/"否"），空字符串表示不限
        salary: 期望薪资
        top_n: 返回前 N 个城市
        tolerance: 薪资浮动范围
        weights: 三个维度的权重，默认 {就业机会:0.5, 满意度:0.3, 薪资:0.2}
        min_group: 城市参与排序所需的最小样本量，默认 config.MIN_RECOMMEND_SAMPLES

    Returns:
        含「就业人数 / 平均满意度 / 平均期望薪资 / 样本是否充足 / 综合适配得分」
        的 DataFrame，按得分降序取前 top_n；筛不出样本返回空 DataFrame。
    """
    min_group = config.MIN_RECOMMEND_SAMPLES if min_group is None else int(min_group)

    full = _score(df, major, edu, intern, salary, tolerance, weights, min_group)
    if full.empty:
        empty = pd.DataFrame()
        empty.attrs["min_group"] = min_group
        empty.attrs["excluded_cities"] = []
        return empty

    result = full.head(top_n).copy()
    result.attrs.update(full.attrs)
    return result


def weight_sensitivity(
    df: pd.DataFrame,
    major: str,
    edu: str = "",
    intern: str = "",
    salary: int | None = None,
    tolerance: int = DEFAULT_SALARY_TOLERANCE,
    min_group: int | None = None,
    scenarios: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, object]]:
    """换几组权重重算排序，量化「排序对权重有多敏感」。

    salary=None（默认）表示不加薪资区间筛选：敏感性分析问的是「权重一改
    排序会不会翻」，用薪资区间把样本筛到只剩一两个城市，这个问题就无从比较了。

    Returns:
        每个权重场景一行：TOP1 城市、与默认排序的 Spearman 秩相关、TOP1 是否一致。
        样本不足时对应行标注「样本不足，无法评估」。
    """
    min_group = config.MIN_RECOMMEND_SAMPLES if min_group is None else int(min_group)
    scenarios = scenarios or DEFAULT_SCENARIOS

    base = _score(
        df, major, edu, intern, salary, tolerance, DEFAULT_WEIGHTS, min_group
    )

    rows: list[dict[str, object]] = []
    for name, weights in scenarios.items():
        row: dict[str, object] = {"权重场景": name}
        other = (
            pd.DataFrame()
            if base.empty
            else _score(df, major, edu, intern, salary, tolerance, weights, min_group)
        )
        if base.empty or other.empty:
            row.update(
                {
                    "TOP1 城市": "N/A",
                    "与默认排序的秩相关": "N/A",
                    "TOP1 是否一致": "样本不足，无法评估",
                }
            )
            rows.append(row)
            continue

        rho = stats.rank_stability(
            base["综合适配得分"], other["综合适配得分"]
        )
        row.update(
            {
                "TOP1 城市": str(other.index[0]),
                "与默认排序的秩相关": round(rho, 3) if rho == rho else "N/A",
                "TOP1 是否一致": "一致" if str(other.index[0]) == str(base.index[0]) else "不一致",
            }
        )
        rows.append(row)

    return rows


def format_recommendation(
    result: pd.DataFrame,
    salary: int,
    weights: dict[str, float] | None = None,
) -> str:
    """把排序结果格式化成可读文本。

    说明文字里的权重取自 weights，必须与 recommend() 传入的一致；
    否则改了权重、文案还在写 0.5/0.3/0.2，就成了「代码与解释不符」。
    """
    min_group = int(
        result.attrs.get("min_group", config.MIN_RECOMMEND_SAMPLES)
        or config.MIN_RECOMMEND_SAMPLES
    )

    if result.empty:
        return (
            "暂无匹配数据。\n"
            f"说明：同条件下每个城市的样本量至少 {min_group} 条才参与排序"
            "（门槛见 src/config.py::MIN_RECOMMEND_SAMPLES）。\n"
            "建议：放宽薪资范围、换一个专业关键词，或取消实习条件后重试。"
        )

    lines = [
        f"===== 适配度 TOP{len(result)} 城市（期望薪资 {salary} 元，描述性排序）=====",
        "",
    ]
    for i, (city, row) in enumerate(result.iterrows(), 1):
        flag = ""
        if not bool(row.get("样本是否充足", True)):
            flag = f"  [样本偏少：低于 {config.MIN_GROUP_SIZE} 条]"
        lines += [
            f"{i}. 城市：{city}{flag}",
            f"   同条件就业人数：{int(row['就业人数'])} 人",
            f"   平均满意度：{row['平均满意度']:.2f} / 5",
            f"   平均期望薪资：{row['平均期望薪资']:.0f} 元",
            f"   综合适配得分：{row['综合适配得分']:.3f}",
            "-" * 40,
        ]

    used = _normalized_weights(weights)
    excluded = [str(c) for c in (result.attrs.get("excluded_cities") or [])]
    lines += [
        "",
        f"说明：综合适配得分 = 就业机会 ×{used['就业机会']:g}"
        f" + 满意度 ×{used['满意度']:g} + 薪资 ×{used['薪资']:g}",
        "     （三个指标已各自归一化到 0~1 后再加权，避免量纲差异导致权重失真）",
        f"     样本量低于 {min_group} 的城市未参与排序"
        + (f"（已排除：{'、'.join(excluded)}）" if excluded else ""),
        f"     「样本偏少」= 同条件就业人数低于 {config.MIN_GROUP_SIZE} 条，均值不稳定，仅供参考",
        "     本结果只是对这批样本的描述性排序（汇总实测到的就业人数/满意度/薪资），"
        "不做任何预测。",
    ]
    return "\n".join(lines)