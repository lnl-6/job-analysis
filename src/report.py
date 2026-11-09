# -*- coding: utf-8 -*-
"""报告层：把「算出来的数字」翻译成控制台输出与 markdown 报告。

本项目的硬规则（写在这儿，也写在 README 与 src/stats.py 里）：

1. **报告里出现的每个数字都来自 results（本次运行的输出）。**
   这里不允许写死任何结论、阈值或数值；阈值一律读 src/config.py，
   并在报告里如实打印「本次用的是多少」，避免「阈值藏在代码里」。
2. **判定不在这里另做。** 显著 / 不显著 / 样本不足都取自
   stats.Comparison.verdict，报告只负责措辞与排版。
3. **只做描述性测量，不做预测。** 报告不预测个体结果、不预测未来；
   样本不足就直说样本不足。
"""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path

import pandas as pd

from src import config, quality, stats
from src.cleaner import CleaningReport
from src.loader import DataSourceInfo


def _signed(value: float) -> str:
    """把差值格式化成 +123 / -123。"""
    return f"{value:+.0f}" if value == value else "N/A"


def build_conclusions(results: dict[str, object]) -> list[str]:
    """把本次运行的统计结果翻译成句子。

    硬规则：这里不允许出现任何写死的数值或结论——每句话的数字都取自 results，
    判定取自 stats.Comparison.verdict。
    """
    conclusions: list[str] = []

    # ---- 成对对比：差值 + 置信区间 + 检验 + 效应量 + 判定 ----
    comparisons: list[stats.Comparison] = results.get("comparisons", [])  # type: ignore[assignment]
    for cmp in comparisons:
        head = (
            f"**{cmp.label}**：{cmp.group_a} 平均 {cmp.mean_a:.0f} 元（n={cmp.n_a}）、"
            f"{cmp.group_b} 平均 {cmp.mean_b:.0f} 元（n={cmp.n_b}）；"
            f"相差 {_signed(cmp.diff)} 元"
        )
        if not cmp.sufficient:
            conclusions.append(
                f"{head}；两组样本量未同时达到门槛 {config.MIN_GROUP_SIZE} → {cmp.verdict}。"
            )
            continue
        detail = (
            f"（95% 置信区间 [{cmp.ci_low:+.0f}, {cmp.ci_high:+.0f}]；"
            f"Welch t 检验 p={stats.p_text(cmp.p_welch)}；"
            f"Mann-Whitney U 检验 p={stats.p_text(cmp.p_mannwhitney)}；"
            f"Cohen's d={cmp.cohens_d:.2f}，{stats.effect_size_label(cmp.cohens_d)}效应）"
        )
        tail = (
            "在本样本中差异显著（显著 ≠ 因果，也不代表总体一定如此）。"
            if cmp.significant
            else "本样本不支持「两组有差异」的判断。"
        )
        conclusions.append(f"{head}，{detail}；→ {cmp.verdict}：{tail}")

    # ---- 专业维度：只有样本量达标的专业才参与排名 ----
    major_df = results.get("major_top")
    if isinstance(major_df, pd.DataFrame) and not major_df.empty:
        enough = (
            major_df[major_df["人数"] >= config.MIN_GROUP_SIZE]
            if "人数" in major_df.columns
            else major_df
        )
        if enough.empty:
            conclusions.append(
                f"**专业维度**：薪资靠前的 {len(major_df)} 个专业里，没有任何一个达到最小样本量 "
                f"{config.MIN_GROUP_SIZE}，因此不给专业排名结论。"
            )
        else:
            items = [
                f"{idx}（{row['平均期望薪资']:.0f} 元，n={int(row['人数'])}）"
                for idx, row in enough.iterrows()
            ]
            shown = min(3, len(items))
            conclusions.append(
                "**专业维度**：在样本量达标的专业中，平均期望薪资从高到低为 "
                + "、".join(items[:shown])
                + f"（共 {len(items)} 个达标，此处列出前 {shown} 个）。"
            )

    # ---- 毕业去向 ----
    direction_df = results.get("direction")
    if isinstance(direction_df, pd.DataFrame) and not direction_df.empty:
        enough_dir = (
            direction_df[direction_df["人数"] >= config.MIN_GROUP_SIZE]
            if "人数" in direction_df.columns
            else direction_df
        )
        if enough_dir.empty:
            conclusions.append(
                f"**毕业去向**：没有任何去向的样本量达到 {config.MIN_GROUP_SIZE}，不给结论。"
            )
        else:
            best = enough_dir["平均期望薪资"].idxmax()
            worst = enough_dir["平均期望薪资"].idxmin()
            conclusions.append(
                f"**毕业去向**：样本量达标的去向里，平均期望薪资最高的是「{best}」"
                f"（{float(enough_dir.loc[best, '平均期望薪资']):.0f} 元），"
                f"最低的是「{worst}」"
                f"（{float(enough_dir.loc[worst, '平均期望薪资']):.0f} 元）。"
            )

    # ---- 专业 × 城市交叉（已按样本量门槛过滤，空白 = 无样本或样本不足）----
    cross_df = results.get("major_city")
    if isinstance(cross_df, pd.DataFrame) and not cross_df.empty:
        counts = results.get("major_city_n")
        # pandas 3 的 stack() 默认保留缺失值；这里显式剔除，否则 idxmax 会报 all NA
        stacked = cross_df.stack()
        stacked = stacked[stacked.notna()]

        def _cell_size(item: tuple[str, str]) -> str:
            if isinstance(counts, pd.DataFrame):
                try:
                    value = counts.loc[item[0], item[1]]
                except KeyError:
                    return "n=?"
                if pd.notna(value):
                    return f"n={int(value)}"
            return "n=?"

        if stacked.empty:
            conclusions.append(
                f"**专业 × 城市**：没有任何组合的样本量达到 {config.MIN_GROUP_SIZE}，"
                "因此不给交叉结论（数据量补足后再看）。"
            )
        else:
            hi = stacked.idxmax()
            lo = stacked.idxmin()
            conclusions.append(
                f"**专业 × 城市**：在「薪资前 {len(cross_df)} 专业 × 主要城市」中，"
                f"只有样本量 ≥ {config.MIN_GROUP_SIZE} 的组合参与比较（其余在表中为空白）；"
                f"平均期望薪资最高的是「{hi[0]} @ {hi[1]}」"
                f"（{float(stacked.max()):.0f} 元，{_cell_size(hi)}），"
                f"最低的是「{lo[0]} @ {lo[1]}」"
                f"（{float(stacked.min()):.0f} 元，{_cell_size(lo)}）。"
            )

    # ---- 整体概况（含均值的 95% 置信区间）----
    ov = results.get("ov")
    if isinstance(ov, dict) and ov:
        mean = float(ov["平均期望薪资"])
        lo_ci = float(ov.get("薪资均值置信区间下界", mean))
        hi_ci = float(ov.get("薪资均值置信区间上界", mean))
        conclusions.append(
            f"**整体概况**：有效样本 {int(ov['样本量'])} 条，平均期望薪资 {mean:.0f} 元"
            f"（95% 置信区间 [{lo_ci:.0f}, {hi_ci:.0f}]，中位数 "
            f"{float(ov['期望薪资中位数']):.0f} 元），"
            f"平均满意度 {float(ov['平均满意度']):.2f} / 5，"
            f"有实习经历者占比 {float(ov['有实习占比']):.1f}%。"
        )

    # ---- 城市排序的稳健性（换权重重算的实测结果）----
    sens = _sensitivity_rows(results)
    if sens:
        flips = [r for r in sens if str(r.get("TOP1 是否一致")) == "不一致"]
        rhos = [
            float(r["与默认排序的秩相关"])
            for r in sens
            if isinstance(r.get("与默认排序的秩相关"), (int, float))
        ]
        rho_txt = (
            f"各场景与默认排序的 Spearman 秩相关在 {min(rhos):.2f} ~ {max(rhos):.2f} 之间"
            if rhos
            else "秩相关无法计算（共有城市少于 2 个）"
        )
        if flips:
            conclusions.append(
                f"**城市排序的稳健性**：换一组权重后，TOP1 城市在 {len(flips)}/{len(sens)} "
                f"个权重场景里发生变化——排序对权重是敏感的，不是数据唯一决定的；{rho_txt}。"
            )
        else:
            conclusions.append(
                f"**城市排序的稳健性**：{len(sens)} 个权重场景下 TOP1 城市都没有变；{rho_txt}。"
            )

    # ---- 数据质量体检里被标记出来的项（现算）----
    quality_result = results.get("quality")
    flagged = (
        quality.flagged_items(quality_result)
        if isinstance(quality_result, dict)
        else []
    )
    if flagged:
        conclusions.append(
            f"**数据质量提示**：体检有 {len(flagged)} 项需要留意——"
            + "；".join(flagged)
            + "。这只说明「这批数据在这些维度上区分不出来」，不代表现实世界如此。"
        )

    return conclusions


def _df_to_markdown(df: pd.DataFrame) -> str:
    """把 DataFrame 转成 markdown 表格（不依赖 tabulate）。"""
    if df.empty:
        return "_（无数据）_"

    frame = df.copy()
    if isinstance(frame.index, pd.Index) and not isinstance(frame.index, pd.RangeIndex):
        frame = frame.reset_index()

    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    sep = "|" + "|".join(["---"] * len(frame.columns)) + "|"
    rows = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
        for row in frame.itertuples(index=False)
    ]
    return "\n".join([header, sep, *rows])


def _comparisons_frame(results: dict[str, object]) -> pd.DataFrame:
    """对比表：一行一次对比，所有列都是算出来的。"""
    comparisons: list[stats.Comparison] = results.get("comparisons", [])  # type: ignore[assignment]
    if not comparisons:
        return pd.DataFrame()
    return pd.DataFrame([c.to_row() for c in comparisons])


def _sensitivity_rows(results: dict[str, object]) -> list[dict[str, object]]:
    """取出权重敏感性对照的逐行结果（analysis.recommend_sensitivity 返回 dict）。"""
    value = results.get("recommend_sensitivity")
    if isinstance(value, dict):
        rows = value.get("rows")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        return []
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    return []


def _sensitivity_query(results: dict[str, object]) -> dict[str, object]:
    """取出敏感性分析实际使用的查询条件（由数据自动选取）。"""
    value = results.get("recommend_sensitivity")
    if isinstance(value, dict):
        query = value.get("query")
        if isinstance(query, dict):
            return query
    return {}


def _sensitivity_query_text(results: dict[str, object]) -> str:
    """把敏感性分析的查询条件写成一行，避免读者不知道「换权重时用的是哪组条件」。"""
    query = _sensitivity_query(results)
    if not query:
        return "_（样本不足，未做权重敏感性分析）_"
    return "查询条件（由数据自动选取，不是预设）：" + "，".join(
        f"{k}={v}" for k, v in query.items()
    ) + "。"


def _sensitivity_frame(results: dict[str, object]) -> pd.DataFrame:
    """权重敏感性表。"""
    rows = _sensitivity_rows(results)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _file_sha256(path: Path, length: int = 12) -> str:
    """数据文件指纹：让「报告里的数字来自哪份数据」可核对。"""
    try:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()[:length]
    except OSError:
        return "N/A"


def _env_text() -> str:
    """本次运行的环境版本（可复现性信息）。"""
    parts = [f"Python {platform.python_version()}", f"pandas {pd.__version__}"]
    try:
        import numpy as np

        parts.append(f"numpy {np.__version__}")
    except Exception:  # noqa: BLE001 - 环境信息拿不到不该影响出报告
        parts.append("numpy 未知")
    try:
        from importlib.metadata import version

        parts.append(f"scipy {version('scipy')}")
    except Exception:  # noqa: BLE001
        parts.append("scipy 未知")
    return "｜".join(parts)


def provenance_lines(info: DataSourceInfo, results: dict[str, object]) -> list[str]:
    """本次运行的元信息：数据指纹、参数、阈值、环境、时间。

    目的：报告里每个数字都能追溯到「哪份数据 + 哪组参数 + 哪套阈值」。
    """
    top_n = len(results.get("major_top", []))  # type: ignore[arg-type]
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    note = config.source_note_for(info.path.name)
    lines = [
        f"- 数据文件：`{info.path}`",
        f"- 数据指纹：SHA-256 前 12 位 `{_file_sha256(info.path)}`",
        f"- 数据规模：{info.rows} 行 × {info.cols} 列（清洗后的有效样本见下一节）",
        f"- 数据性质：{info.label}",
    ]
    # 换数据源最容易犯的错：把岗位薪资当成受访者期望薪资继续讲。
    # 所以口径声明必须紧贴在「数据性质」旁边，而不是只写在 README 里。
    if note:
        lines.append(f"- 数据口径：**{note[0]}** —— {note[1]}")
    lines += [
        f"- 运行参数：专业维度取前 {top_n} 名",
        f"- 判定阈值：最小样本量 {config.MIN_GROUP_SIZE}｜显著性水平 α={config.ALPHA}｜"
        f"置信水平 {config.CI_LEVEL:.0%}｜IQR 系数 {config.IQR_FACTOR}",
        f"- 运行环境：{_env_text()}",
        f"- 生成时间：{stamp}",
    ]
    return lines


def _quality_frame(results: dict[str, object]) -> pd.DataFrame:
    """数据质量体检的主表（检查项 / 本次实测 / 判定）。"""
    quality_result = results.get("quality")
    checks = quality_result.get("checks") if isinstance(quality_result, dict) else None
    if not isinstance(checks, list) or not checks:
        return pd.DataFrame()
    return pd.DataFrame([check.to_row() for check in checks])


def _missing_frame(results: dict[str, object]) -> pd.DataFrame:
    """各列缺失率（%），按缺失率降序。"""
    quality_result = results.get("quality")
    rates = quality_result.get("missing") if isinstance(quality_result, dict) else None
    if not isinstance(rates, dict) or not rates:
        return pd.DataFrame()
    frame = pd.DataFrame({"列": list(rates), "缺失率（%）": list(rates.values())})
    return frame.sort_values("缺失率（%）", ascending=False).reset_index(drop=True)


def write_markdown_report(
    info: DataSourceInfo,
    report: CleaningReport,
    results: dict[str, object],
    conclusions: list[str],
    charts: dict[str, Path],
) -> Path:
    """生成 output/report.md。"""
    out_dir = config.ensure_output_dir()
    path = out_dir / "report.md"

    note = config.source_note_for(info.path.name)
    title = f"# 就业数据分析报告（{note[0]}）" if note else "# 就业数据分析报告"
    lines: list[str] = [
        title,
        "",
        "> 本报告由 `main.py` 自动生成：**下面每一个数字（样本量、均值、差值、95% 置信区间、"
        "p 值、效应量、判定）都是本次运行从数据里算出来的**。",
        "> 程序内不含任何预设结论，也不包含任何预测——只对这批样本做描述性测量。",
        "",
        "## 一、本次运行信息（可追溯）",
        "",
    ]
    lines += provenance_lines(info, results)

    if info.is_demo:
        lines += [
            "",
            "> [!] **本报告基于合成演示数据生成，只用于验证代码流程，不得作为真实结论引用。**",
            "> 该数据的模拟参数全部公开在 `data/README.md` 的「演示数据的模拟场景」一节；"
            "必要时可用 `python scripts/generate_demo_data.py --show-params` 复核。",
        ]

    lines += ["", "## 二、数据清洗", ""]
    lines += [f"- {line}" for line in report.summary_lines()]

    lines += [
        "",
        "## 三、对比与显著性检验",
        "",
        f"判定规则：两组样本量都 ≥ {config.MIN_GROUP_SIZE} 才给结论；在满足样本量的前提下，"
        f"Holm 校正后的 p < {config.ALPHA} 记「差异显著」，否则记「差异不显著」。"
        "样本不足的对比只报告样本量，不给结论。",
        f"同一次运行共 {len(results.get('comparisons') or [])} 组对比，作为一个检验族做 Holm 校正"
        "（只做一组对比时，校正 p 等于原始 p）。",
        "",
        _df_to_markdown(_comparisons_frame(results)),
        "",
        "> 均值差 = A 组均值 − B 组均值；置信区间由 Welch–Satterthwaite 自由度算出；"
        "Cohen's d 是效应量（0.2 小 / 0.5 中 / 0.8 大）。",
        "> **显著 ≠ 因果，也不等于总体如此**：这里只报告「本样本里两组均值差多大、有多不确定」。",
        "",
        "## 四、核心结论（由上一节与明细表推导）",
        "",
    ]
    lines += [f"{i}. {c}" for i, c in enumerate(conclusions, 1)]

    lines += ["", "## 五、分析明细", ""]
    lines += ["### 5.1 学历维度", "", _df_to_markdown(results["edu"]), ""]  # type: ignore[arg-type]
    lines += ["### 5.2 实习经历维度", "", _df_to_markdown(results["intern"]), ""]  # type: ignore[arg-type]
    lines += [
        f"### 5.3 专业维度（薪资前 {len(results['major_top'])}）",  # type: ignore[arg-type]
        "",
        _df_to_markdown(results["major_top"]),  # type: ignore[arg-type]
        "",
    ]
    lines += ["### 5.4 毕业去向维度", "", _df_to_markdown(results["direction"]), ""]  # type: ignore[arg-type]
    lines += ["### 5.5 城市维度（主要城市）", "", _df_to_markdown(results["city"]), ""]  # type: ignore[arg-type]
    lines += [
        "### 5.6 专业 × 城市（平均期望薪资）",
        "",
        _df_to_markdown(results["major_city"]),  # type: ignore[arg-type]
        "",
        f"> 空白表示该组合「无样本」或「样本量低于 {config.MIN_GROUP_SIZE}」——"
        "小样本均值不作为结果展示（不是 0 元）。",
        "",
        "### 5.7 专业 × 城市（该组合的样本量）",
        "",
        _df_to_markdown(results.get("major_city_n", pd.DataFrame())),  # type: ignore[arg-type]
        "",
        "### 5.8 一线 vs 新一线",
        "",
        _df_to_markdown(
            pd.DataFrame([results["tier"]])  # type: ignore[list-item]
        ),
        "",
        "### 5.9 城市排序的权重敏感性（换权重重算，看排序会不会翻）",
        "",
        _sensitivity_query_text(results),
        "",
        _df_to_markdown(_sensitivity_frame(results)),
        "",
        "> 每一行的排序都是当次现算的；秩相关为 Spearman 相关系数，越接近 1 说明排序越不受权重影响。",
        "> 城市列里的「无 / 未填」等占位值不是城市，已从排序中排除（见 src/config.py::CITY_UNSPECIFIED）。",
        "",
    ]

    lines += ["## 六、图表", ""]
    if charts:
        for name, chart_path in charts.items():
            rel = chart_path.name
            lines += [f"### {name}", "", f"![{name}]({rel})", ""]
    else:
        lines.append("_（未生成图表）_")

    lines += [
        "",
        "## 七、数据质量体检（自动算出的可疑之处）",
        "",
        "在给结论之前，先检查这批数据本身是否站得住：缺失、重复、取值集中度，"
        "以及「这个维度到底有没有区分度」。全部现算，不含任何预设判断。",
        "",
        "### 7.1 缺失率",
        "",
        _df_to_markdown(_missing_frame(results)),
        "",
        "### 7.2 体检结论",
        "",
        _df_to_markdown(_quality_frame(results)),
        "",
        f"> 判定阈值：组间解释力 eta² < {quality.ETA2_WEAK} 记「几乎不解释差异」；"
        f"缺失率 > {quality.MISSING_WARN:.0f}% 记「偏高」；"
        f"单一取值占比 ≥ {quality.DOMINANT_SHARE:.0f}% 记「过度集中」。",
        "> eta² = 组间平方和 / 总平方和（单因素 ANOVA）：0 = 该维度完全不能区分，1 = 完全区分。",
        "> 体检只报现象：不解释原因，也不预测。",
        "",
        "## 八、方法与局限",
        "",
        "**数据清洗**",
        "",
        "- 无效值：期望薪资 ≤ 0 的行视为无效记录，直接剔除（不参与填充与统计）。",
        "- 缺失值：分类变量用众数、连续变量用中位数填充（中位数抗异常值）。",
        f"- 异常值：对「期望薪资」用 IQR 法（Q1−{config.IQR_FACTOR}·IQR ~ Q3+{config.IQR_FACTOR}·IQR）"
        "剔除，具体剔除行数见第二节（报告不复述固定比例）。",
        "",
        "**统计方法**",
        "",
        "- 均值差 + 95% 置信区间：Welch–Satterthwaite 自由度下的区间估计（不假设方差相等）。",
        "- 显著性检验：Welch t 检验（不等方差）；另用 Mann-Whitney U 秩检验做稳健性对照"
        "（薪资右偏，秩检验不依赖正态假设）。",
        "- 效应量：Cohen's d（合并标准差），用于区分「显著但很小」和「显著且明显」。",
        f"- 最小样本量门槛：任一组低于 {config.MIN_GROUP_SIZE} 条即不给结论。",
        "",
        "**多重比较**",
        "",
        "- 同一次运行的多组对比按 Holm 法校正 p 值（family-wise error rate），"
        "避免组数一多就靠 α 刷出假阳性。",
        "",
        "**数据质量体检**",
        "",
        "- 第七节用单因素 ANOVA 的 eta²（组间平方和 / 总平方和）判断每个维度对指标的区分度，"
        "只报现象，不解释原因。",
        "",
        "**不做预测**",
        "",
        "- 本报告不含任何预测内容：没有趋势外推、没有个体结果预测、没有「应该去哪」的建议。",
        "- 「城市排序」一节是对已收集样本的描述性排序（就业人数 / 满意度 / 薪资三个实测指标），"
        "不是预测，也不构成任何决策建议。",
        "",
        "**局限**",
        "",
        "- 描述性统计只说明「相关」，**不能推出因果**。",
        "- 样本若非随机抽样，结论不可外推到总体。",
        "- IQR 剔除会去掉一部分高端样本，平均值可能被低估；剔除行数已在上文列出。",
    ]
    if info.is_demo:
        lines += [
            "- 本报告用的是**合成演示数据**，分组差异是模拟场景参数造成的，"
            "不代表任何现实结论。",
        ]

    lines += [
        "",
        "---",
        "",
        "本文件由程序生成（`output/report.md`）。同一份数据文件 + 同一组参数重跑 "
        "`python main.py`，可复现同一批数字（仅「生成时间」不同）。",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def print_console_report(
    info: DataSourceInfo,
    report: CleaningReport,
    results: dict[str, object],
    conclusions: list[str],
    charts: dict[str, Path],
) -> None:
    """在控制台打印摘要（不弹窗、不阻塞；只用可安全打印的字符）。"""
    sep = "=" * 62

    print(sep)
    print("大学生就业数据分析")
    print(sep)
    print(f"数据来源：{info.describe()}")
    if info.is_demo:
        print("[!]  当前使用的是【合成演示数据】，结论不可用于真实引用！")
    print()

    print("-- 本次运行信息 --")
    for line in provenance_lines(info, results):
        print("  " + line.lstrip("- "))
    print()

    print("-- 数据清洗 --")
    for line in report.summary_lines():
        print("  " + line)
    print()

    print("-- 对比与显著性检验 --")
    frame = _comparisons_frame(results)
    print(frame.to_string(index=False) if not frame.empty else "  （无可比分组）")
    print()

    print("-- 核心结论（全部现算） --")
    for i, c in enumerate(conclusions, 1):
        # 控制台去掉 markdown 的 ** 标记
        print(f"  {i}. {c.replace('**', '')}")
    print()

    print("-- 分析明细 --")
    for title, key in [
        ("学历维度", "edu"),
        ("实习经历维度", "intern"),
        (f"专业维度（薪资前 {len(results['major_top'])}）", "major_top"),  # type: ignore[arg-type]
        ("毕业去向维度", "direction"),
        ("城市维度", "city"),
        ("专业×城市交叉（均值）", "major_city"),
        ("专业×城市交叉（样本量）", "major_city_n"),
    ]:
        value = results.get(key)
        if not isinstance(value, pd.DataFrame) or value.empty:
            print(f"\n[{title}]\n  （无数据）")
            continue
        print(f"\n[{title}]")
        print(value.to_string())

    print("\n-- 城市排序的权重敏感性 --")
    sens = _sensitivity_frame(results)
    print(sens.to_string(index=False) if not sens.empty else "  （样本不足，无法评估）")

    print("\n-- 已生成图表 --")
    for name, p in charts.items():
        print(f"  {name}: {p}")

    print(sep)