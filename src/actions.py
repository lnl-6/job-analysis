"""行动层：把报告里的统计表机械地翻译成「投什么、去哪投、补什么」。

三条约束（与项目铁律一致）：
1. **只描述、不预测**：每条建议都由 ``analysis.run_all()`` 的数字直接推出，
   换一批数据重跑就会变，没有任何模型、没有外推。
2. **每张卡都带证据与出处**：``evidence`` 是要写进报告的数字，
   ``source`` 是这些数字在 results / SQL / 文档里的位置，评审可顺着核对。
3. **样本不足就明说**：某维度没有达标分组时，卡片照出，但强度降为「弱」
   并写明「等重抓数据」，绝不拿小样本下结论。

卡片字段：
    key       稳定标识（写进 report.json，供 BI 与 CI 复用）
    title     一句话标题
    finding   发现了什么（带数字）
    action    建议怎么做（描述性，不是预测）
    evidence  支撑数字，元组
    strength  强度判定：强 / 中 / 弱
    source    出处，元组
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import pandas as pd

from src import config, stats

STRENGTH_STRONG = "强"
STRENGTH_MEDIUM = "中"
STRENGTH_WEAK = "弱"
ALLOWED_STRENGTHS: tuple[str, ...] = (STRENGTH_STRONG, STRENGTH_MEDIUM, STRENGTH_WEAK)


@dataclass(frozen=True)
class ActionCard:
    """一张行动卡：发现 → 建议 → 证据 → 出处。"""

    key: str
    title: str
    finding: str
    action: str
    evidence: tuple[str, ...] = ()
    strength: str = STRENGTH_WEAK
    source: tuple[str, ...] = ()

    def to_row(self) -> dict[str, str]:
        return {
            "卡片": self.title,
            "发现": self.finding,
            "建议": self.action,
            "强度": self.strength,
            "证据": "；".join(self.evidence),
            "出处": "；".join(self.source),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "finding": self.finding,
            "action": self.action,
            "strength": self.strength,
            "evidence": list(self.evidence),
            "source": list(self.source),
        }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _groups(results: dict[str, object], dimension: str, metric: str = "p50") -> pd.DataFrame:
    """从 group_stats 里取某个维度的达标分组，按指标降序。"""
    table = results.get("group_stats")
    if not isinstance(table, pd.DataFrame) or table.empty:
        return pd.DataFrame(columns=["group_value", "sample_size", metric, "mean_salary"])
    if "dimension" not in table.columns:
        return pd.DataFrame(columns=["group_value", "sample_size", metric, "mean_salary"])

    part = table[table["dimension"] == dimension].copy()
    if part.empty:
        return part
    if "sample_ok" in part.columns:
        part = part[part["sample_ok"] == 1]
    if metric not in part.columns:
        metric = "mean_salary" if "mean_salary" in part.columns else part.columns[-1]
    return part.sort_values(metric, ascending=False).reset_index(drop=True)


def _overall_p50(results: dict[str, object]) -> float:
    ov = results.get("ov")
    if isinstance(ov, pd.Series) and "期望薪资中位数" in ov.index:
        return float(ov["期望薪资中位数"])
    if isinstance(ov, dict) and "期望薪资中位数" in ov:
        return float(ov["期望薪资中位数"])
    return float("nan")


def _comparison(results: dict[str, object], key: str) -> stats.Comparison | None:
    items = results.get("comparisons")
    if not isinstance(items, (list, tuple)):
        return None
    for item in items:
        if getattr(item, "key", "") == key:
            return item  # type: ignore[return-value]
    return None


def _strength_from_d(cohens_d: float) -> str:
    """效应量决定强度：|d| ≥ 0.5 强，≥ 0.2 中，其余弱。"""
    if cohens_d != cohens_d:  # NaN
        return STRENGTH_WEAK
    magnitude = abs(cohens_d)
    if magnitude >= 0.5:
        return STRENGTH_STRONG
    if magnitude >= 0.2:
        return STRENGTH_MEDIUM
    return STRENGTH_WEAK


# ---------------------------------------------------------------------------
# 七张卡
# ---------------------------------------------------------------------------
def _direction_card(results: dict[str, object]) -> ActionCard | None:
    groups = _groups(results, "major")
    if groups.empty:
        return None
    overall = _overall_p50(results)
    top = groups.iloc[0]
    runner = groups.iloc[1] if len(groups) > 1 else None
    best = str(top["group_value"])
    best_p50 = float(top["p50"])
    action = f"主攻「{best}」方向"
    if runner is not None:
        action += f"，次选「{runner['group_value']}」（P50 {float(runner['p50']):.0f} 元）"
    if best_p50 != best_p50 or overall != overall:
        strength = STRENGTH_WEAK
    elif best_p50 >= overall * 1.10 and float(top["sample_size"]) >= config.MIN_GROUP_SIZE:
        strength = STRENGTH_STRONG
    elif best_p50 >= overall:
        strength = STRENGTH_MEDIUM
    else:
        strength = STRENGTH_WEAK
    return ActionCard(
        key="direction",
        title="投递方向优先级",
        finding=(
            f"「{best}」P50 {best_p50:.0f} 元、样本 {int(top['sample_size'])} 条；"
            f"整体 P50 {overall:.0f} 元"
        ),
        action=action,
        evidence=(f"{best} n={int(top['sample_size'])}", f"P50={best_p50:.0f}", f"整体 P50={overall:.0f}"),
        strength=strength,
        source=("ads_group_stats(dimension=major)", "报告 5.3 专业薪资"),
    )


def _city_card(results: dict[str, object]) -> ActionCard | None:
    groups = _groups(results, "city")
    if groups.empty:
        return None
    overall = _overall_p50(results)
    total = float(groups["sample_size"].sum()) or 1.0
    scored = groups.copy()
    scored["机会指数"] = (scored["sample_size"] / total) * (scored["p50"] / overall)
    scored = scored.sort_values("机会指数", ascending=False).reset_index(drop=True)
    first = scored.iloc[0]
    second = scored.iloc[1] if len(scored) > 1 else None

    action = f"首投「{first['group_value']}」"
    if second is not None:
        action += f"，备选「{second['group_value']}」"
    if float(first["机会指数"]) >= 0.08:
        strength = STRENGTH_MEDIUM
    else:
        strength = STRENGTH_WEAK
    return ActionCard(
        key="city",
        title="投递城市优先级",
        finding=(
            f"机会指数最高的是「{first['group_value']}」"
            f"（n={int(first['sample_size'])}、P50 {float(first['p50']):.0f} 元、指数 {float(first['机会指数']):.3f}）"
        ),
        action=action,
        evidence=(
            "机会指数 = 样本占比 × (P50 / 整体 P50)",
            f"{first['group_value']} 指数 {float(first['机会指数']):.3f}",
        ),
        strength=strength,
        source=("ads_group_stats(dimension=city)", "报告 5.5 城市对比"),
    )


def _city_tier_card(results: dict[str, object]) -> ActionCard | None:
    item = _comparison(results, "city_tier1_vs_new_tier1")
    if item is None:
        return None
    diff = float(item.diff)
    if item.significant:
        action = "一线显著更高 → 优先一线；但要接受更高的生活成本"
        strength = _strength_from_d(float(item.cohens_d))
    else:
        action = "校正后不显著 → 按生活成本与岗位匹配度选，不要为「一线」额外加价"
        strength = STRENGTH_WEAK
    return ActionCard(
        key="city_tier",
        title="一线 vs 新一线",
        finding=(
            f"均值差 {diff:+.0f} 元（n={item.n_a}/{item.n_b}），Holm p={stats.p_text(item.p_holm)}，"
            f"d={float(item.cohens_d):+.2f}；判定：{item.verdict}"
        ),
        action=action,
        evidence=(f"均值差 {diff:+.0f}", f"Holm p={stats.p_text(item.p_holm)}", f"d={float(item.cohens_d):+.2f}"),
        strength=strength,
        source=("ads_comparisons(city_tier1_vs_new_tier1)", "报告 3. 对比与显著性检验"),
    )


def _education_card(results: dict[str, object]) -> ActionCard | None:
    groups = _groups(results, "education")
    # 「不限」不是学历档位，不能参与「相邻档位差」的比较
    if not groups.empty and "group_value" in groups.columns:
        groups = groups[~groups["group_value"].astype(str).str.strip().isin(["不限", "", "nan"])]
    if groups.empty or len(groups) < 2:
        return None
    ordered = groups.sort_values("p50", ascending=False).reset_index(drop=True)
    top, runner = ordered.iloc[0], ordered.iloc[1]
    gap = float(top["p50"]) - float(runner["p50"])
    item = _comparison(results, "edu_master_vs_bachelor")
    significant = bool(item.significant) if item is not None else False
    if significant and gap > 0:
        action = "相邻学历档确有溢价 → 学历投资有边际回报"
        strength = _strength_from_d(float(item.cohens_d))
    elif gaps_small(gap):
        action = "相邻档位差距很小 → 学历不是主要变量，优先补技能与项目"
        strength = STRENGTH_MEDIUM
    else:
        action = "高学历组样本不足或未达标 → 先补样本再谈学历回报"
        strength = STRENGTH_WEAK
    return ActionCard(
        key="education",
        title="学历投入的边际回报",
        finding=(
            f"P50 最高「{top['group_value']}」{float(top['p50']):.0f} 元"
            f"（n={int(top['sample_size'])}），次高「{runner['group_value']}」"
            f"{float(runner['p50']):.0f} 元，差 {gap:+.0f} 元"
        ),
        action=action,
        evidence=(f"档位差 {gap:+.0f}", f"最高档 n={int(top['sample_size'])}"),
        strength=strength,
        source=("ads_group_stats(dimension=education)", "ads_comparisons(edu_*)"),
    )


def gaps_small(gap: float) -> bool:
    """相邻学历档差值不到 500 元视为「没差多少」。"""
    return abs(gap) < 500.0


def _internship_card(results: dict[str, object]) -> ActionCard | None:
    layered = results.get("intern_by_job_type")
    if not isinstance(layered, pd.DataFrame) or layered.empty:
        return None
    if not {"岗位类型", "是否实习", "平均期望薪资", "人数"}.issubset(layered.columns):
        return None
    rows = layered.copy()
    pivot = rows.pivot_table(index="岗位类型", columns="是否实习", values="平均期望薪资", aggfunc="mean")
    if pivot.empty:
        return None
    candidates: list[tuple[str, float]] = []
    for job_type in pivot.index:
        pair = pivot.loc[job_type]
        if "是" not in pair.index or "否" not in pair.index:
            continue
        yes, no = float(pair["是"]), float(pair["否"])
        if yes != yes or no != no:  # 任一侧没样本（NaN）时不算差值
            continue
        candidates.append((str(job_type), yes - no))
    if not candidates:
        return None
    job_type, diff = sorted(candidates, key=lambda item: abs(item[1]), reverse=True)[0]
    action = (
        "同类型岗位里实习薪资明显更低 → 实习优先看转正通道与项目密度，不要看薪资"
        if diff < 0
        else "同类型岗位里实习薪资不低于正式岗 → 实习可直接作为投递入口"
    )
    strength = STRENGTH_MEDIUM if abs(diff) >= 1000 else STRENGTH_WEAK
    return ActionCard(
        key="internship",
        title="实习岗值不值得优先",
        finding=f"「{job_type}」类型内：实习 vs 非实习均值差 {diff:+.0f} 元（已按岗位类型分层）",
        action=action,
        evidence=(f"{job_type} 分层差 {diff:+.0f}", f"分层组数 {len(candidates)}"),
        strength=strength,
        source=("results.intern_by_job_type", "报告 5.x 分层表"),
    )


def _robustness_card(results: dict[str, object]) -> ActionCard | None:
    payload = results.get("recommend_sensitivity")
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return None

    changed = 0
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        total += 1
        same = str(row.get("TOP1 是否一致", "")).strip()
        if same and same != "一致" and same != "样本不足，无法评估":
            changed += 1

    credible = changed == 0
    action = (
        "城市 TOP1 在所有权重场景下都稳定 → 城市结论可全信"
        if credible
        else f"{changed}/{total} 组权重下 TOP1 会变 → 城市结论只能说「量级可信、排序需谨慎」"
    )
    return ActionCard(
        key="robustness",
        title="城市结论的稳健性",
        finding=f"{total} 组权重场景中，TOP1 变化 {changed} 次",
        action=action,
        evidence=(f"变化 {changed}/{total}",),
        strength=STRENGTH_STRONG if credible else (STRENGTH_MEDIUM if changed == 1 else STRENGTH_WEAK),
        source=("results.recommend_sensitivity", "报告 5.9 权重敏感性"),
    )


def _credibility_card(results: dict[str, object], meta: dict[str, Any] | None) -> ActionCard | None:
    problems: list[str] = []
    evidence: list[str] = []
    strength = STRENGTH_STRONG

    table = results.get("group_stats")
    if isinstance(table, pd.DataFrame) and not table.empty and "sample_ok" in table.columns:
        missing = int((table["sample_ok"] == 0).sum())
        if missing:
            problems.append(f"{missing} 个分组样本不足 {config.MIN_GROUP_SIZE}")
            evidence.append(f"不足分组 {missing}/{len(table)}")
            strength = STRENGTH_MEDIUM if strength == STRENGTH_STRONG else strength

    quality_result = results.get("quality")
    if isinstance(quality_result, dict):
        checks = quality_result.get("checks") or []
        flagged = [c for c in checks if getattr(c, "flagged", False)]
        if flagged:
            problems.append(f"数据质量体检有 {len(flagged)} 项提示")
            evidence.append(f"体检提示 {len(flagged)} 项")
            strength = STRENGTH_MEDIUM if strength == STRENGTH_STRONG else strength

    if isinstance(meta, dict):
        coverage = (meta.get("major_coverage") or {}) if isinstance(meta.get("major_coverage"), dict) else {}
        ratio = coverage.get("coverage")
        if isinstance(ratio, (int, float)):
            evidence.append(f"归类覆盖率 {float(ratio):.1%}")
            if float(ratio) < config.MAJOR_COVERAGE_WARN:
                problems.append(f"方向归类覆盖率 {float(ratio):.1%} 低于阈值")
                strength = STRENGTH_WEAK
        salary_cov = meta.get("salary_coverage")
        if isinstance(salary_cov, dict) and isinstance(salary_cov.get("ratio"), (int, float)):
            evidence.append(f"薪资可解析 {float(salary_cov['ratio']):.1%}")
        quota = meta.get("quota_ratio")
        if isinstance(quota, (int, float)):
            evidence.append(f"配额完成率 {float(quota):.1%}")
            if float(quota) < 0.6:
                problems.append(f"配额完成率仅 {float(quota):.1%}（抓取被反爬中断）")
                strength = STRENGTH_WEAK

    if not problems:
        return ActionCard(
            key="credibility",
            title="这批结论能下到什么程度",
            finding="没有发现明显的样本或口径缺陷",
            action="可以按报告结论行动；仍要记住这是单时点快照",
            evidence=tuple(evidence) or ("无异常项",),
            strength=STRENGTH_STRONG,
            source=("results.quality", "数据集旁注 meta", "报告 4.1 / 7.x"),
        )
    return ActionCard(
        key="credibility",
        title="这批结论能下到什么程度",
        finding="；".join(problems),
        action="按「先补样本、再看结论」的顺序行动：上面带「弱」的卡片先别当决策依据",
        evidence=tuple(evidence),
        strength=strength,
        source=("results.quality", "results.group_stats", "数据集旁注 meta"),
    )


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def build_actions(results: dict[str, object], meta: dict[str, Any] | None = None) -> list[ActionCard]:
    """把 results 机械地翻译成行动卡列表（空 results 返回空列表）。"""
    if not results:
        return []

    builders: Sequence[Iterable[ActionCard | None]] = (
        (_direction_card(results),),
        (_city_card(results),),
        (_city_tier_card(results),),
        (_education_card(results),),
        (_internship_card(results),),
        (_robustness_card(results),),
        (_credibility_card(results, meta),),
    )
    cards = [card for group in builders for card in group if card is not None]
    return cards


def to_frame(cards: Sequence[ActionCard]) -> pd.DataFrame:
    """行动卡表格（报告与控制台共用同一张表）。"""
    columns = ["卡片", "发现", "建议", "强度", "证据", "出处"]
    if not cards:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([card.to_row() for card in cards], columns=columns)


def to_json(cards: Sequence[ActionCard]) -> list[dict[str, Any]]:
    """机器可读形态（写进 output/report.json）。"""
    return [card.to_dict() for card in cards]