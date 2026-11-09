#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清洗与分析逻辑的测试。

两种运行方式：
    python tests/test_pipeline.py      # 不依赖 pytest，直接跑
    pytest tests/test_pipeline.py      # 若装了 pytest

只测「容易悄悄错」的地方：清洗的不变量、评分归一化、结论与数据一致。
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from scripts import generate_demo_data  # noqa: E402
from src import analysis, cleaner, config, quality, recommender, report, stats  # noqa: E402
from src.cleaner import (  # noqa: E402
    clean,
    drop_invalid_salary,
    fill_missing,
    remove_salary_outliers,
)
from src.loader import DataLoadError, DataSourceInfo, load_data  # noqa: E402


def make_df() -> pd.DataFrame:
    """构造一个小而可控的测试集（含缺失值和异常值）。

    注意：故意带一个用不到的「性别」列，用来验证代码能容忍多余列。
    """
    return pd.DataFrame({
        "性别": ["男", "女", None, "男", "女", "男"],
        config.COL_EDU: ["本科", "硕士", "本科", "博士", "硕士", "本科"],
        config.COL_MAJOR: ["计算机", "计算机", "金融", "计算机", "金融", "金融"],
        config.COL_SALARY: [8000, 12000, 7000, 999999, None, 9000],  # 999999 是异常值
        config.COL_SATISFACTION: [4.0, 4.5, 3.0, 5.0, None, 3.5],
        config.COL_INTERN: ["是", "是", None, "否", "否", "是"],
        config.COL_DIRECTION: ["就业", "就业", "考研", "就业", "待业", "就业"],
        config.COL_CITY: ["上海", "北京", "武汉", "上海", "南京", "深圳"],
    })


def test_fill_missing() -> None:
    df = make_df()
    filled, report = fill_missing(df)

    assert len(filled) == len(df), "填充不应改变行数"
    assert filled[config.COL_SALARY].isna().sum() == 0, "薪资应无缺失"
    assert filled[config.COL_INTERN].isna().sum() == 0, "实习经历应无缺失"
    assert config.COL_SALARY in report, "薪资缺失应被记录"
    print("  [OK] test_fill_missing")


def test_remove_outliers() -> None:
    df = make_df()
    filled, _ = fill_missing(df)
    cleaned, removed, bounds = remove_salary_outliers(filled)

    assert removed >= 1, "应至少剔除 1 个异常值（999999）"
    assert len(cleaned) <= len(filled), "剔除后行数不应变多"
    assert cleaned[config.COL_SALARY].max() < 999999, "异常值应被剔除"
    lo, hi = bounds
    assert lo < hi, "IQR 区间下界应小于上界"
    assert cleaned[config.COL_SALARY].between(lo, hi).all(), "剔除后不应有越界值"
    print("  [OK] test_remove_outliers")


def test_drop_invalid_salary() -> None:
    """期望薪资 ≤ 0 的行必须被剔除；真正的缺失值不在这里处理（交给填充）。"""
    df = make_df().copy()
    df.loc[0, config.COL_SALARY] = -2100.0
    df.loc[1, config.COL_SALARY] = 0.0
    original_missing = int(df[config.COL_SALARY].isna().sum())

    valid, removed = drop_invalid_salary(df)

    assert removed == 2, f"应剔除 2 行非正薪资，实际 {removed}"
    assert len(valid) + removed == len(df), "行数应守恒"
    assert (valid[config.COL_SALARY].dropna() > 0).all(), "不应残留非正薪资"
    assert int(valid[config.COL_SALARY].isna().sum()) == original_missing, (
        "真正的缺失值不应被这一步剔除"
    )

    _, report = clean(df)
    assert report.invalid_salary_removed == 2, "清洗报告应记录无效薪资行数"
    assert any("无效薪资行剔除" in line for line in report.summary_lines()), (
        "清洗摘要应包含该步骤"
    )
    print("  [OK] test_drop_invalid_salary")


def test_clean_end_to_end() -> None:
    cleaned, report = clean(make_df())

    assert len(cleaned) > 0, "清洗后不应为空"
    assert report.original_rows == 6
    assert report.final_rows == len(cleaned)
    assert report.removed_rows >= 1
    print("  [OK] test_clean_end_to_end")


def test_analysis_shape() -> None:
    cleaned, _ = clean(make_df())
    results = analysis.run_all(cleaned)

    assert "ov" in results and results["ov"]["样本量"] == len(cleaned)
    edu_df = results["edu"]
    assert config.COL_SALARY not in edu_df.columns  # 应为重命名后的列
    assert "平均期望薪资" in edu_df.columns
    assert "人数" in edu_df.columns
    print("  [OK] test_analysis_shape")


def test_edu_ordering() -> None:
    """学历维度应按 本科 → 硕士 → 博士 排序，而非字典序。"""
    df = pd.DataFrame({
        "性别": ["男"] * 3,
        config.COL_EDU: ["博士", "本科", "硕士"],
        config.COL_MAJOR: ["计算机"] * 3,
        config.COL_SALARY: [10000, 6000, 8000],
        config.COL_SATISFACTION: [4.0, 3.0, 3.5],
        config.COL_INTERN: ["是"] * 3,
        config.COL_DIRECTION: ["就业"] * 3,
        config.COL_CITY: ["上海"] * 3,
    })
    result = analysis.edu_analysis(df)
    assert list(result.index) == ["本科", "硕士", "博士"], f"实际顺序：{list(result.index)}"
    print("  [OK] test_edu_ordering")


def test_major_city_cross() -> None:
    """专业 × 城市交叉：行序、列序、已知单元格与空组合都要正确。"""
    cleaned, _ = clean(make_df())
    # min_n=1：这个 fixture 每组只有 1~2 条，先关掉样本量门槛验证取值本身；
    # 门槛行为单独测在 test_cross_min_n_gate
    cross = analysis.major_city_cross(cleaned, top_majors=5, min_n=1)

    assert not cross.empty, "交叉结果不应为空"
    assert list(cross.columns) == config.MAIN_CITIES, f"列应为主要城市，实际：{list(cross.columns)}"
    assert list(cross.index) == ["计算机", "金融"], f"专业应按整体薪资降序，实际：{list(cross.index)}"

    # 已知单元格
    assert cross.loc["计算机", "上海"] == 8000.0, f"实际：{cross.loc['计算机', '上海']}"
    assert cross.loc["计算机", "北京"] == 12000.0, f"实际：{cross.loc['计算机', '北京']}"
    assert cross.loc["金融", "深圳"] == 9000.0, f"实际：{cross.loc['金融', '深圳']}"

    # 无样本组合必须是 NaN，而不是 0
    assert pd.isna(cross.loc["计算机", "武汉"]), "无样本组合应为 NaN"
    assert pd.isna(cross.loc["金融", "广州"]), "无样本组合应为 NaN"

    # top_majors 生效
    only_one = analysis.major_city_cross(cleaned, top_majors=1, min_n=1)
    assert list(only_one.index) == ["计算机"], f"top_majors=1 只应保留薪资最高的专业，实际：{list(only_one.index)}"

    print("  [OK] test_major_city_cross")


def test_major_city_cross_empty() -> None:
    """主要城市无样本时，应返回空 DataFrame 而不是抛异常。"""
    cleaned, _ = clean(make_df())
    no_main_city = cleaned[cleaned[config.COL_CITY] == "杭州"]  # 杭州不在 MAIN_CITIES 中

    cross = analysis.major_city_cross(no_main_city)
    assert cross.empty, "无主要城市样本时应返回空 DataFrame"
    print("  [OK] test_major_city_cross_empty")


def test_recommender_normalization() -> None:
    """归一化后，得分必须落在 [0,1]，且权重真正起作用。"""
    df = pd.DataFrame({
        "性别": ["男"] * 6,
        config.COL_EDU: ["本科"] * 6,
        config.COL_MAJOR: ["计算机"] * 6,
        config.COL_SALARY: [7000, 7100, 8000, 8100, 9000, 9100],
        config.COL_SATISFACTION: [3.0, 3.1, 4.0, 4.1, 5.0, 5.0],
        config.COL_INTERN: ["是"] * 6,
        config.COL_DIRECTION: ["就业"] * 6,
        config.COL_CITY: ["上海", "上海", "北京", "北京", "深圳", "深圳"],
    })

    # min_group=1：fixture 每个城市只有 2 条，先关掉推荐门槛验证归一化与权重
    result = recommender.recommend(
        df, major="计算机", edu="本科", intern="是", salary=8000, min_group=1
    )
    assert not result.empty, "应返回推荐结果"
    score_col = "综合适配得分"
    assert result[score_col].between(0, 1).all(), f"得分应在 [0,1]，实际：{result[score_col].tolist()}"
    assert result[score_col].is_monotonic_decreasing, "结果应按得分降序"
    print("  [OK] test_recommender_normalization")


def test_data_load_error_message() -> None:
    """找不到数据时，报错信息必须给出可执行的解决方法。"""
    try:
        load_data("__not_exist__.csv")
    except DataLoadError as exc:
        msg = str(exc)
        assert "generate_demo_data.py" in msg, "报错应提示如何生成数据"
        print("  [OK] test_data_load_error_message")
        return
    raise AssertionError("不存在的文件应当抛 DataLoadError")


def test_recommender_empty_result() -> None:
    """筛不出任何样本时返回空 DataFrame 并给出可读提示，而不是抛异常。"""
    cleaned, _ = clean(make_df())
    empty = recommender.recommend(cleaned, major="考古学", salary=8000)

    assert empty.empty, "无匹配样本时应返回空 DataFrame"
    text = recommender.format_recommendation(empty, 8000)
    assert "暂无匹配数据" in text, "空结果应给出可读提示"
    print("  [OK] test_recommender_empty_result")


def test_recommender_bad_weights() -> None:
    """权重之和为 0 必须显式报错，而不是静默输出全 0 分。"""
    cleaned, _ = clean(make_df())
    try:
        recommender.recommend(
            cleaned,
            major="计算机",
            weights={"就业机会": 0.0, "满意度": 0.0, "薪资": 0.0},
        )
    except ValueError as exc:
        assert "权重" in str(exc), f"报错信息应说明权重问题，实际：{exc}"
        print("  [OK] test_recommender_bad_weights")
        return
    raise AssertionError("权重之和为 0 时应抛 ValueError")


def test_recommender_weight_text() -> None:
    """结果说明里的权重必须来自实际传入的权重，不能写死 0.5/0.3/0.2。"""
    cleaned, _ = clean(make_df())
    custom = {"就业机会": 1.0, "满意度": 1.0, "薪资": 2.0}  # 归一化后 0.25/0.25/0.5
    result = recommender.recommend(cleaned, major="计算机", weights=custom, min_group=1)

    text = recommender.format_recommendation(result, 8000, weights=custom)
    assert "×0.25" in text, f"说明文字应反映实际权重，实际：{text}"
    assert "×0.5" in text, f"说明文字应反映实际权重，实际：{text}"
    print("  [OK] test_recommender_weight_text")


def test_run_all_top_n() -> None:
    """run_all 的 top_n 必须同时贯通「专业 TOP」与「专业 × 城市」交叉。"""
    cleaned, _ = clean(make_df())
    results = analysis.run_all(cleaned, top_n=2)

    assert len(results["major_top"]) == 2, "专业维度应取前 2 名"  # type: ignore[arg-type]
    assert len(results["major_city"]) == 2, "交叉分析应同步取前 2 个专业"  # type: ignore[arg-type]
    print("  [OK] test_run_all_top_n")


def test_stats_comparison_invariants() -> None:
    """对比检验的基本性质：区间包住差值、方向正确、判定与 p 值一致。"""
    a = pd.Series([10000 + (i % 5) for i in range(40)])
    b = pd.Series([8000 + (i % 5) for i in range(40)])
    cmp = stats.compare(a, b, label="测试组", group_a="A", group_b="B")

    assert cmp.n_a == 40 and cmp.n_b == 40
    assert cmp.diff > 0, "A 组明显更高，差值应为正"
    assert cmp.ci_low < cmp.diff < cmp.ci_high, "差值必须落在自己的置信区间内"
    assert cmp.cohens_d > 0, "效应量方向应与差值一致"
    assert cmp.sufficient, "两组样本量都达到门槛"
    assert cmp.verdict == "差异显著", f"差这么多应判显著，实际：{cmp.verdict}"

    row = cmp.to_row()
    for key in ("对比", "均值差", "95% 置信区间", "p（Welch）", "Cohen's d", "判定"):
        assert key in row, f"对比表缺少列：{key}"

    same = stats.compare(a, a, label="自身", group_a="A", group_b="A")
    assert not same.significant, "同一组数据与自己比较不应判显著"
    assert same.ci_low <= 0 <= same.ci_high, "无差异时置信区间应包含 0"
    print("  [OK] test_stats_comparison_invariants")


def test_stats_insufficient_group() -> None:
    """样本量不够时必须拒绝下结论，而不是给一个「看起来显著」的数字。"""
    small = pd.Series([30000, 31000, 29000, 30500, 29500])
    big = pd.Series([8000 + (i % 7) for i in range(40)])
    cmp = stats.compare(small, big, label="小样本", group_a="小", group_b="大")

    assert not cmp.sufficient, "n=5 不应通过样本量门槛"
    assert not cmp.significant, "样本不足时不得判显著"
    assert "样本不足" in cmp.verdict, f"判定应说明样本不足，实际：{cmp.verdict}"
    print("  [OK] test_stats_insufficient_group")


def test_cross_min_n_gate() -> None:
    """交叉表：样本量不足的组合必须留空，样本量要能单独查。"""
    cleaned, _ = clean(make_df())

    gated = analysis.major_city_cross(cleaned, top_majors=5)
    assert gated.isna().all().all(), "fixture 每组样本都远低于门槛，应全部留空"

    ungated = analysis.major_city_cross(cleaned, top_majors=5, min_n=1)
    assert ungated.notna().any().any(), "关掉门槛后应能看到取值"

    counts = analysis.major_city_counts(cleaned, top_majors=5)
    assert counts.loc["计算机", "上海"] == 1, f"实际：{counts.loc['计算机', '上海']}"
    print("  [OK] test_cross_min_n_gate")


def test_recommender_min_group_gate() -> None:
    """推荐门槛：默认门槛下小样本城市不参与排序，调低门槛后才出现并标注样本偏少。"""
    cleaned, _ = clean(make_df())

    default = recommender.recommend(cleaned, major="计算机", salary=8000)
    assert default.empty, "默认门槛（>= 5 条）下不应有城市入选"

    loose = recommender.recommend(cleaned, major="计算机", salary=8000, min_group=1)
    assert not loose.empty, "门槛降到 1 后应能选出城市"
    assert not bool(loose["样本是否充足"].iloc[0]), "样本量远低于 30，应标注为样本偏少"
    assert "样本偏少" in recommender.format_recommendation(loose, 8000)
    print("  [OK] test_recommender_min_group_gate")


def test_recommender_sensitivity_rows() -> None:
    """权重敏感性：每个权重场景都要产出一行可核对的对照结果。"""
    cleaned, _ = clean(make_df())
    rows = recommender.weight_sensitivity(
        cleaned, major="计算机", salary=8000, min_group=1
    )

    assert len(rows) == len(recommender.DEFAULT_SCENARIOS), "每个权重场景应有一行"
    for row in rows:
        for key in ("权重场景", "TOP1 城市", "与默认排序的秩相关", "TOP1 是否一致"):
            assert key in row, f"敏感性表缺少列：{key}"
    assert rows[0]["TOP1 是否一致"] == "一致", "默认场景与自身比较，TOP1 必然一致"
    print("  [OK] test_recommender_sensitivity_rows")


def test_null_scenario_is_effect_free() -> None:
    """null 场景必须真的没有分组效应，否则「校准对照」没有意义。"""
    params = generate_demo_data.scenario_params("null")
    assert params["neutral"] is True
    assert len(set(params["edu_base"].values())) == 1, "null 场景各学历基准必须相同"
    assert set(params["city_mult"].values()) == {1.0}, "null 场景城市系数必须全为 1"
    assert set(params["major_mult"].values()) == {1.0}, "null 场景专业系数必须全为 1"
    assert set(params["intern_factor"].values()) == {1.0}
    assert set(params["direction_factor"].values()) == {1.0}

    df = generate_demo_data.generate(rows=200, seed=1, scenario="null")
    assert len(df) == 200
    assert df[config.COL_SALARY].isna().sum() > 0, "缺失值仍应被注入（清洗逻辑要能被验证）"

    cleaned, _ = clean(df)
    result = analysis.comparisons(cleaned)
    assert result, "null 场景也应能算出若干组对比"
    bad = [item.label for item in result if item.significant]
    assert not bad, f"按定义无分组效应的数据不应出现「差异显著」，实际：{bad}"

    effects = generate_demo_data.scenario_params("effects")
    assert effects["neutral"] is False
    assert effects["edu_base"]["博士"] > effects["edu_base"]["本科"]
    print("  [OK] test_null_scenario_is_effect_free")


def test_report_is_computed_not_predicted() -> None:
    """报告：带溯源信息、明确「不做预测」，小样本只标样本不足而不硬下结论。"""
    df = make_df()
    cleaned, cleaning = clean(df)
    results = analysis.run_all(cleaned)
    conclusions = report.build_conclusions(results)

    assert "样本不足" in "\n".join(conclusions), "小样本必须标注样本不足，而不是硬给结论"

    info = DataSourceInfo(
        path=config.DEMO_DATA_PATH, rows=len(df), cols=df.shape[1], is_demo=True
    )
    original_output_dir = config.OUTPUT_DIR
    with tempfile.TemporaryDirectory() as tmp:
        config.OUTPUT_DIR = Path(tmp)
        try:
            md_path = report.write_markdown_report(
                info, cleaning, results, conclusions, {}
            )
            body = md_path.read_text(encoding="utf-8")
        finally:
            config.OUTPUT_DIR = original_output_dir

    assert "都是本次运行从数据里算出来的" in body, "报告应声明所有数字由本次运行算出"
    assert "不含任何预测内容" in body, "报告应明确不含任何预测"
    assert "Welch" in body and "95% 置信区间" in body, "报告应含统计检验信息"
    print("  [OK] test_report_is_computed_not_predicted")


def test_readme_keeps_no_run_specific_numbers() -> None:
    """README 里不允许出现任何一次运行的具体结果数字（避免「预设结论」）。"""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    banned = [
        "10394", "6956", "7916", "7207", "8494", "7108",
        "9085", "8521", "6231", "49.4%", "19.5%", "5.5%",
    ]
    hits = [value for value in banned if value in readme]
    assert not hits, f"README 不应复述某次运行的结果数字，实际出现：{hits}"
    print("  [OK] test_readme_keeps_no_run_specific_numbers")


def test_quality_audit_shape() -> None:
    """体检结果必须包含行数、缺失率表与体检结论。"""
    df = make_df()
    result = quality.audit(df)

    assert result["rows"] == len(df), "体检应记录行数"
    assert isinstance(result["missing"], dict) and result["missing"], "应有缺失率表"
    checks = result["checks"]
    assert isinstance(checks, list) and len(checks) >= 5, "体检项太少"
    assert all(hasattr(c, "to_row") for c in checks), "体检项应能转成表格行"
    print("  [OK] test_quality_audit_shape")


def test_quality_flags_flat_dimension() -> None:
    """某指标是常量列（没有区分度）时，必须被标成「提示」。"""
    df = make_df().copy()
    df[config.COL_SATISFACTION] = 3.0

    result = quality.audit(df)
    flagged = quality.flagged_items(result)

    assert any("满意度取值分布" in item for item in flagged), (
        f"常量列应被点名，实际：{flagged}"
    )
    assert any("几乎不解释差异" in c.verdict for c in result["checks"]), (
        "组间解释力为 0 时应给出提示"
    )
    print("  [OK] test_quality_flags_flat_dimension")


def test_holm_adjust_monotonic() -> None:
    """Holm 校正：不小于原始 p、不超过 1、保持单调，NaN 原样保留。"""
    raw = [0.001, 0.02, 0.2, float("nan")]
    adjusted = stats.holm_adjust(raw)

    assert math.isnan(adjusted[3]), "无法计算的 p 值应原样保留"
    assert all(p >= r for p, r in zip(adjusted[:3], raw[:3])), "校正后不应小于原始 p"
    assert all(p <= 1.0 for p in adjusted[:3]), "校正后不应超过 1"
    assert adjusted[:3] == sorted(adjusted[:3]), "校正后应保持单调"
    assert math.isclose(adjusted[0], 0.003), f"最小 p 应乘 3，实际 {adjusted[0]}"
    assert math.isclose(adjusted[1], 0.04), f"第二小应乘 2，实际 {adjusted[1]}"
    assert math.isclose(adjusted[2], 0.2), f"最大 p 应乘 1，实际 {adjusted[2]}"
    print("  [OK] test_holm_adjust_monotonic")


def test_group_stats_table_shape() -> None:
    """分组统计长表（写进 SQL 的 ads_group_stats）：列齐全、样本量都为正。"""
    df = make_df()
    df_clean, _report = cleaner.clean(df)
    table = analysis.group_stats(df_clean)

    expected = {
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
    }
    assert expected <= set(table.columns), f"缺列：{sorted(expected - set(table.columns))}"
    assert (table["sample_size"] > 0).all(), "分组的样本量必须为正"
    assert table["internship_rate"].between(0, 100).all(), "实习率必须是百分比"


def test_comparisons_have_stable_keys() -> None:
    """每个对比项都要有稳定 key（写进 SQL 的 comparison_key）且不重复。"""
    df = make_df()
    df_clean, _report = cleaner.clean(df)
    items = analysis.comparisons(df_clean)

    keys = [item.key for item in items]
    assert all(keys), f"对比项缺少稳定 key：{keys}"
    assert len(keys) == len(set(keys)), f"对比 key 重复：{keys}"
    for item in items:
        assert item.verdict, "判定文案不能为空（样本不足也要写明样本不足）"


def main() -> int:
    print("=" * 50)
    print("运行测试")
    print("=" * 50)

    tests = [
        test_fill_missing,
        test_remove_outliers,
        test_drop_invalid_salary,
        test_clean_end_to_end,
        test_analysis_shape,
        test_edu_ordering,
        test_major_city_cross,
        test_major_city_cross_empty,
        test_recommender_normalization,
        test_recommender_empty_result,
        test_recommender_bad_weights,
        test_recommender_weight_text,
        test_run_all_top_n,
        test_data_load_error_message,
        test_stats_comparison_invariants,
        test_stats_insufficient_group,
        test_cross_min_n_gate,
        test_recommender_min_group_gate,
        test_quality_audit_shape,
        test_quality_flags_flat_dimension,
        test_holm_adjust_monotonic,
        test_recommender_sensitivity_rows,
        test_null_scenario_is_effect_free,
        test_report_is_computed_not_predicted,
        test_readme_keeps_no_run_specific_numbers,
        test_group_stats_table_shape,
        test_comparisons_have_stable_keys,
    ]

    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {exc}")

    print("=" * 50)
    if failed:
        print(f"失败 {failed} / {len(tests)}")
        return 1
    print(f"全部通过（{len(tests)} / {len(tests)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
