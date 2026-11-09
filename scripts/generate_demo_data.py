#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成演示用的大学生就业数据集（合成数据）。

⚠️ 关于「结果是不是预先定好的」——本脚本的态度：

    这份数据是**模拟场景**：它的全部参数都写在下面、会随生成一起打印出来，
    属于「数据说明」，不是「分析结论」。
    脚本不预设任何结论：报告里的均值、差值、置信区间、p 值和判定，
    一律由 src/analysis.py + src/stats.py 在运行时从数据里算出来。

    为了能自证「管线不会凭空发现差异」，脚本提供两个场景：

        effects（默认）：按公开参数注入已知效应，用于演示完整输出。
        null          ：**所有分组效应都压成中性值**（学历基准相同、城市/专业
                        系数 1.0、实习与去向量为 1.0、满意度与分组无关），
                        用作校准对照——在这个场景上若报出「差异显著」就是假阳性。

    参数表与场景说明见 data/README.md 的「演示数据的模拟场景」一节。

用法：
    python scripts/generate_demo_data.py
    python scripts/generate_demo_data.py --rows 2000 --seed 7
    python scripts/generate_demo_data.py --scenario null --out /tmp/null.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "raw" / "demo_大学生就业数据.csv"

# ---- 各维度基准值（模拟场景参数：公开、可核对、可替换）----
EDU_BASE_SALARY = {"本科": 6000, "硕士": 8500, "博士": 12000}
EDU_PROB = [0.70, 0.25, 0.05]

CITY_MULTIPLIER = {
    "上海": 1.25, "北京": 1.25, "深圳": 1.25,
    "广州": 1.15, "杭州": 1.10,
    "南京": 0.95, "武汉": 0.95, "成都": 0.95,
    "其他": 0.80, "无": 0.90,
}
CITY_PROB = [0.14, 0.14, 0.12, 0.11, 0.09, 0.08, 0.08, 0.08, 0.09, 0.07]

MAJOR_MULTIPLIER = {
    "计算机": 1.30, "电子信息": 1.20, "临床医学": 1.15,
    "金融": 1.10, "数学": 1.00,
    "机械": 0.95, "法学": 0.95, "土木工程": 0.90,
    "英语": 0.85, "市场营销": 0.85,
}
MAJOR_LIST = list(MAJOR_MULTIPLIER.keys())
MAJOR_PROB = [0.15, 0.12, 0.08, 0.10, 0.07, 0.12, 0.08, 0.09, 0.09, 0.10]

DIRECTION_LIST = ["就业", "考研", "出国", "创业", "待业"]
DIRECTION_PROB = [0.55, 0.25, 0.08, 0.05, 0.07]

# 实习经历 / 毕业去向对期望薪资的乘数
INTERN_FACTOR = {"是": 1.08, "否": 1.0}
DIRECTION_FACTOR = {"就业": 1.0, "考研": 0.90, "出国": 1.0, "创业": 1.0, "待业": 0.80}

# 个人浮动区间（相对基准的乘数）
INDIVIDUAL_NOISE = (0.85, 1.15)

# 人为注入的缺失比例与异常值比例（用于验证清洗逻辑，与分组无关）
NA_RATIO = {"性别": 0.04, "期望薪资": 0.05, "是否有实习经历": 0.03}
OUTLIER_RATIO = 0.02
OUTLIER_RANGE = (3.0, 4.5)

SCENARIOS = ("effects", "null")


def scenario_params(scenario: str = "effects") -> dict[str, object]:
    """返回该场景实际使用的全部效应参数（公开、可断言、可打印）。

    null 场景把所有分组效应都压成中性值，因此数据里不存在任何「预设差异」，
    可作为假阳性校准的对照组。
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"未知场景：{scenario}（可选：{'、'.join(SCENARIOS)}）")

    neutral = scenario == "null"
    return {
        "scenario": scenario,
        "neutral": neutral,
        "edu_base": {k: (6000 if neutral else v) for k, v in EDU_BASE_SALARY.items()},
        "city_mult": {k: (1.0 if neutral else v) for k, v in CITY_MULTIPLIER.items()},
        "major_mult": {k: (1.0 if neutral else v) for k, v in MAJOR_MULTIPLIER.items()},
        "intern_factor": {k: (1.0 if neutral else v) for k, v in INTERN_FACTOR.items()},
        "direction_factor": {
            k: (1.0 if neutral else v) for k, v in DIRECTION_FACTOR.items()
        },
        "individual_noise": INDIVIDUAL_NOISE,
        "na_ratio": dict(NA_RATIO),
        "outlier_ratio": OUTLIER_RATIO,
        "outlier_range": OUTLIER_RANGE,
    }


def generate(rows: int = 1200, seed: int = 42, scenario: str = "effects") -> pd.DataFrame:
    """生成合成就业数据集。

    Args:
        rows: 行数
        seed: 随机种子（固定以保证可复现）
        scenario: effects=按公开参数注入已知效应；null=无分组效应的校准对照

    Returns:
        含 8 列的 DataFrame（含人为注入的缺失值与异常值）。
    """
    params = scenario_params(scenario)
    neutral = bool(params["neutral"])
    edu_base = params["edu_base"]  # type: ignore[assignment]
    city_mult_table = params["city_mult"]  # type: ignore[assignment]
    major_mult_table = params["major_mult"]  # type: ignore[assignment]
    intern_factor = params["intern_factor"]  # type: ignore[assignment]
    direction_factor = params["direction_factor"]  # type: ignore[assignment]

    rng = np.random.default_rng(seed)

    gender = rng.choice(["男", "女"], size=rows, p=[0.52, 0.48])
    edu = rng.choice(list(EDU_BASE_SALARY), size=rows, p=EDU_PROB)
    major = rng.choice(MAJOR_LIST, size=rows, p=MAJOR_PROB)
    city = rng.choice(list(CITY_MULTIPLIER), size=rows, p=CITY_PROB)
    direction = rng.choice(DIRECTION_LIST, size=rows, p=DIRECTION_PROB)
    has_intern = rng.choice(["是", "否"], size=rows, p=[0.60, 0.40])

    # 基础薪资 = 学历基准 × 城市系数 × 专业系数（null 场景下三者都是中性值）
    base = np.array([edu_base[e] for e in edu], dtype=float)
    city_mult = np.array([city_mult_table[c] for c in city], dtype=float)
    major_mult = np.array([major_mult_table[m] for m in major], dtype=float)
    salary = base * city_mult * major_mult

    # 实习经历乘数（effects: 是 +8%）
    salary = np.array([intern_factor[i] for i in has_intern]) * salary

    # 个人浮动
    salary *= rng.uniform(INDIVIDUAL_NOISE[0], INDIVIDUAL_NOISE[1], size=rows)

    # 毕业去向乘数（effects: 待业 0.80 / 考研 0.90）
    salary = np.array([direction_factor[d] for d in direction]) * salary

    # 期望薪资取整到百位
    salary = np.round(salary / 100) * 100

    if neutral:
        # null 场景：满意度与分组、薪资都无关（否则又会造出「预设差异」）
        satisfaction = 3.2 + rng.normal(0, 0.8, size=rows)
    else:
        # 满意度：与薪资分位、去向、实习相关
        salary_pct = pd.Series(salary).rank(pct=True).to_numpy()
        satisfaction = 2.6 + salary_pct * 1.4
        satisfaction += np.where(has_intern == "是", 0.25, -0.10)
        satisfaction += np.where(direction == "就业", 0.35, 0.0)
        satisfaction -= np.where(direction == "待业", 0.70, 0.0)
        satisfaction += rng.normal(0, 0.35, size=rows)

    satisfaction = np.clip(np.round(satisfaction, 1), 0.0, 5.0)

    df = pd.DataFrame({
        "性别": gender,
        "学历": edu,
        "专业": major,
        "期望薪资": salary,
        "满意度评分": satisfaction,
        "是否有实习经历": has_intern,
        "毕业去向": direction,
        "工作城市": city,
    })

    # ---- 人为注入缺失值（用于验证清洗逻辑，注入位置随机、与分组无关）----
    def inject_na(col: str, ratio: float) -> None:
        n = int(rows * ratio)
        idx = rng.choice(rows, size=n, replace=False)
        df.loc[idx, col] = np.nan

    for col, ratio in NA_RATIO.items():
        inject_na(col, ratio)

    # ---- 人为注入明显异常值（极少数超高期望薪资）----
    n_outliers = max(1, int(rows * OUTLIER_RATIO))
    out_idx = rng.choice(rows, size=n_outliers, replace=False)
    df.loc[out_idx, "期望薪资"] = df.loc[out_idx, "期望薪资"] * rng.uniform(
        OUTLIER_RANGE[0], OUTLIER_RANGE[1], size=n_outliers
    )

    return df


def describe_params(params: dict[str, object]) -> list[str]:
    """把场景参数整理成可打印/可写入文档的几行。"""
    keys = (
        ("edu_base", "学历基准薪资"),
        ("city_mult", "城市系数"),
        ("major_mult", "专业系数"),
        ("intern_factor", "实习乘数"),
        ("direction_factor", "去向乘数"),
        ("individual_noise", "个人浮动区间"),
        ("na_ratio", "注入缺失比例"),
        ("outlier_ratio", "注入异常值比例"),
        ("outlier_range", "异常值倍数区间"),
    )
    return [f"{label}：{params[key]}" for key, label in keys]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成演示用大学生就业数据集（合成数据，参数全部公开）"
    )
    parser.add_argument("--rows", type=int, default=1200, help="生成行数，默认 1200")
    parser.add_argument("--seed", type=int, default=42, help="随机种子，默认 42（固定以保证可复现）")
    parser.add_argument(
        "--scenario",
        type=str,
        default="effects",
        choices=list(SCENARIOS),
        help="effects=注入已知效应（默认）；null=无分组效应的校准对照",
    )
    parser.add_argument("--out", type=str, default=str(OUT_PATH), help="输出路径")
    parser.add_argument(
        "--show-params",
        action="store_true",
        help="只打印本场景的参数表，不生成数据",
    )
    args = parser.parse_args()

    params = scenario_params(args.scenario)
    if args.show_params:
        print(f"[i] 场景：{args.scenario}")
        for line in describe_params(params):
            print(f"    {line}")
        return 0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    df = generate(rows=args.rows, seed=args.seed, scenario=args.scenario)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"[OK] 已生成合成数据：{out_path}")
    print(f"     场景：{args.scenario}｜行数：{len(df)}｜列数：{df.shape[1]}｜种子：{args.seed}")
    print(f"     缺失值：\n{df.isnull().sum().to_string()}")
    print()
    print("[i] 本场景的模拟参数（属于数据说明，不是分析结论）：")
    for line in describe_params(params):
        print(f"    {line}")
    print()
    print("[!] 提醒：这是合成数据，请勿用于真实结论引用。")
    print("    报告里的所有数字（含 p 值与置信区间）由 src/analysis.py 在运行时算出。")
    print("    真实数据请放 data/raw/大学生就业数据.csv，程序会优先加载它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())