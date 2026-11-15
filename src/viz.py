# -*- coding: utf-8 -*-
"""可视化层：每张图都保存到 output/，不调用 plt.show()。

原脚本的问题：9 处 plt.show() 会在启动时连弹 9 个阻塞窗口。
现在统一改为 savefig，既可以放进 README，也可以在无界面环境（服务器/CI）运行。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")  # 必须在 pyplot 之前设置

import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402

from src import config  # noqa: E402

PALETTE = ["#63AFE0", "#AE91C9", "#4AD4A1", "#FF8888", "#FFDD55", "#77DDFF"]


def _save(fig: plt.Figure, name: str) -> Path:
    """统一保存图片，关闭 figure 释放内存。"""
    out_dir = config.ensure_output_dir()
    path = out_dir / name
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def _annotate_bars(ax, values, offset: float = 0.0, fmt: str = "{:.0f}") -> None:
    """给柱状图加数值标签。"""
    for i, v in enumerate(values):
        if pd.notna(v):
            ax.text(i, v + offset, fmt.format(v), ha="center", fontsize=11)


def edu_salary_bar(edu_df: pd.DataFrame) -> Path:
    """不同学历的平均期望薪资。"""
    fig, ax = plt.subplots(figsize=(8, 6))
    x = list(edu_df.index)
    y = edu_df["平均期望薪资"].tolist()
    ax.bar(x, y, color=PALETTE[: len(x)])
    ax.set_xlabel("学历", fontsize=13)
    ax.set_ylabel("平均期望薪资（元）", fontsize=13)
    ax.set_title("不同学历的平均期望薪资", fontsize=15, pad=15)
    _annotate_bars(ax, y, offset=max(y) * 0.02 if y else 0)
    ax.margins(y=0.12)
    return _save(fig, "01_学历_平均期望薪资.png")


def edu_satisfaction_bar(edu_df: pd.DataFrame) -> Path:
    """不同学历的平均满意度。"""
    fig, ax = plt.subplots(figsize=(8, 5))
    x = list(edu_df.index)
    y = edu_df["平均满意度"].tolist()
    ax.bar(x, y, color=PALETTE[3:6] if len(x) <= 3 else PALETTE)
    ax.set_xlabel("学历", fontsize=13)
    ax.set_ylabel("平均满意度评分", fontsize=13)
    ax.set_title("不同学历的平均就业满意度", fontsize=15, pad=15)
    ax.set_ylim(0, 5)
    _annotate_bars(ax, y, offset=0.08, fmt="{:.2f}")
    return _save(fig, "02_学历_平均满意度.png")


def intern_boxplot(df: pd.DataFrame) -> Path:
    """实习经历对期望薪资分布的影响（箱线图）。"""
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.boxplot(
        x=config.COL_INTERN,
        y=config.COL_SALARY,
        data=df,
        hue=config.COL_INTERN,
        palette="Set2",
        legend=False,
        ax=ax,
    )
    ax.set_xlabel("是否有实习经历", fontsize=13)
    ax.set_ylabel("期望薪资（元）", fontsize=13)
    ax.set_title("实习经历对期望薪资分布的影响", fontsize=15)
    return _save(fig, "03_实习经历_薪资分布.png")


def major_salary_bar(major_df: pd.DataFrame) -> Path:
    """平均期望薪资前 N 的专业。"""
    fig, ax = plt.subplots(figsize=(10, 6))
    x = major_df.index.tolist()
    y = major_df["平均期望薪资"].tolist()
    ax.bar(x, y, color="#ffbb78")
    ax.set_xlabel("专业", fontsize=13)
    ax.set_ylabel("平均期望薪资（元）", fontsize=13)
    ax.set_title(f"平均期望薪资前 {len(x)} 的专业", fontsize=15)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    _annotate_bars(ax, y, offset=max(y) * 0.02 if y else 0)
    ax.margins(y=0.12)
    return _save(fig, "04_专业_平均薪资TOP.png")


def direction_salary_bar(direction_df: pd.DataFrame) -> Path:
    """不同毕业去向的平均期望薪资。"""
    fig, ax = plt.subplots(figsize=(10, 6))
    x = direction_df.index.tolist()
    y = direction_df["平均期望薪资"].tolist()
    ax.bar(x, y, color="#99BBFF")
    ax.set_xlabel("毕业去向", fontsize=13)
    ax.set_ylabel("平均期望薪资（元）", fontsize=13)
    ax.set_title("不同毕业去向的平均期望薪资", fontsize=15)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    _annotate_bars(ax, y, offset=max(y) * 0.02 if y else 0)
    ax.margins(y=0.12)
    return _save(fig, "05_毕业去向_平均薪资.png")


def satisfaction_boxplot(df: pd.DataFrame) -> Path:
    """不同毕业去向的满意度分布。"""
    n_cat = df[config.COL_DIRECTION].nunique()
    fig, ax = plt.subplots(figsize=(10, 6))
    sns.boxplot(
        x=config.COL_DIRECTION,
        y=config.COL_SATISFACTION,
        data=df,
        hue=config.COL_DIRECTION,
        palette=sns.color_palette("Set2", n_colors=max(n_cat, 1)),
        legend=False,
        ax=ax,
    )
    ax.set_xlabel("毕业去向", fontsize=13)
    ax.set_ylabel("就业满意度评分（0-5）", fontsize=13)
    ax.set_title("不同毕业去向的满意度分布", fontsize=15, pad=15)
    ax.set_ylim(0, 5)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    return _save(fig, "06_毕业去向_满意度分布.png")


def city_salary_bar(city_df: pd.DataFrame) -> Path:
    """主要城市的平均期望薪资。"""
    fig, ax = plt.subplots(figsize=(10, 6))
    x = city_df.index.tolist()
    y = city_df["平均期望薪资"].tolist()
    ax.bar(x, y, color="#77DDFF")
    ax.set_xlabel("工作城市", fontsize=13)
    ax.set_ylabel("平均期望薪资（元）", fontsize=13)
    ax.set_title("主要工作城市的平均期望薪资", fontsize=15)
    _annotate_bars(ax, y, offset=max(y) * 0.02 if y else 0)
    ax.margins(y=0.12)
    return _save(fig, "07_城市_平均薪资.png")


def tier_comparison_bar(tier: dict[str, float]) -> Path:
    """一线 vs 新一线，平均期望薪资对比。"""
    fig, ax = plt.subplots(figsize=(7, 5))
    labels = ["一线城市", "新一线城市"]
    values = [tier["一线平均"], tier["新一线平均"]]
    ax.bar(labels, values, color=["#4CAF50", "#FF9800"])
    ax.set_ylabel("平均期望薪资（元）", fontsize=13)
    ax.set_title("一线 vs 新一线 平均期望薪资", fontsize=15)
    _annotate_bars(ax, values, offset=max(values) * 0.02 if values else 0, fmt="{:.0f}")
    ax.margins(y=0.12)
    return _save(fig, "08_一线vs新一线_薪资.png")


def major_city_heatmap(cross_df: pd.DataFrame) -> Path:
    """专业 × 城市：平均期望薪资热力图。

    无样本的组合（NaN）不标注数值，颜色也为空白，避免把缺失当成 0 元。
    """
    fig, ax = plt.subplots(figsize=(10, 6))
    annot = cross_df.map(lambda v: "" if pd.isna(v) else f"{v:.0f}")
    sns.heatmap(
        cross_df,
        annot=annot,
        fmt="",
        cmap="YlGnBu",
        linewidths=0.5,
        cbar_kws={"label": "平均期望薪资（元）"},
        ax=ax,
    )
    ax.set_xlabel("工作城市", fontsize=13)
    ax.set_ylabel("专业", fontsize=13)
    ax.set_title("专业 × 城市：平均期望薪资热力图", fontsize=15, pad=15)
    plt.setp(ax.get_xticklabels(), rotation=0)
    plt.setp(ax.get_yticklabels(), rotation=0)
    return _save(fig, "09_专业城市_薪资热力图.png")


def render_all(df: pd.DataFrame, results: dict[str, object]) -> dict[str, Path]:
    """按顺序渲染全部图表，返回 {名称: 路径}。"""
    config.setup_chinese_font()

    charts: dict[str, Path] = {}
    charts["学历_薪资"] = edu_salary_bar(results["edu"])            # type: ignore[arg-type]
    charts["学历_满意度"] = edu_satisfaction_bar(results["edu"])    # type: ignore[arg-type]
    charts["实习_箱线图"] = intern_boxplot(df)
    charts["专业_薪资"] = major_salary_bar(results["major_top"])    # type: ignore[arg-type]
    charts["去向_薪资"] = direction_salary_bar(results["direction"])  # type: ignore[arg-type]
    charts["去向_满意度"] = satisfaction_boxplot(df)
    charts["城市_薪资"] = city_salary_bar(results["city"])          # type: ignore[arg-type]
    charts["一线vs新一线"] = tier_comparison_bar(results["tier"])   # type: ignore[arg-type]
    cross_df: pd.DataFrame = results["major_city"]  # type: ignore[assignment]
    if not cross_df.empty:
        charts["专业城市_热力图"] = major_city_heatmap(cross_df)

    return charts
