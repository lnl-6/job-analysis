# -*- coding: utf-8 -*-
"""数据源适配：Boss 直聘岗位数据 → 项目既有的分析口径。

这个模块只做三件事，别的都不做：

1. **解析**：把抓取到的原始 JSON（`wapi/zpgeek/search/joblist.json` 里的 `jobList` 条目）
   转成一行结构化记录；
2. **映射**：把岗位字段映射到项目既有的 7 列分析契约（口径见 `docs/data_source_boss.md`）——
   **这是本项目从「问卷数据」切到「岗位数据」时唯一允许改语义的地方**；
3. **落盘**：输出 analysis-ready 的 CSV，并保留岗位侧扩展列。

明确不做：不抓取（见 `scripts/crawl_boss.py`）、不统计（见 `src/analysis.py`）、不做预测。

⚠️ 语义平移（务必记住，否则会把岗位口径误读成问卷口径）：
    | 分析契约列   | 问卷口径（原数据源）        | 岗位口径（本适配层）                     |
    | ------------ | --------------------------- | ---------------------------------------- |
    | 学历         | 受访者学历                  | 岗位要求的学历（不限/其他 归为「不限」） |
    | 专业         | 所学专业                    | **岗位方向**（数据分析/数据开发/算法…）  |
    | 期望薪资     | 受访者期望月薪              | **岗位薪资区间中值（元/月）**            |
    | 满意度评分   | 受访者满意度                | **岗位吸引力代理分（0~5，proxy）**       |
    | 是否有实习经历 | 在校是否实习过            | 岗位是否面向实习/应届                    |
    | 毕业去向     | 就业/考研/出国/创业/待业     | 岗位类型（实习/校招/社招）               |
    | 工作城市     | 期望工作城市                | 岗位工作城市                             |
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from src import config

# =============================================================================
# 常量
# =============================================================================
#: 岗位方向关键词表（顺序即优先级：先命中先归类）
MAJOR_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("数据分析", ("数据分析", "商业分析", "业务分析", "数据运营", "数据支持", "BI", "bi")),
    ("数据开发", ("数据开发", "数据仓库", "数仓", "ETL", "etl", "数据工程", "大数据开发", "数据平台")),
    ("算法", ("算法", "机器学习", "深度学习", "数据挖掘", "NLP", "自然语言", "计算机视觉", "视觉", "推荐", "大模型", "AI")),
    ("数据治理", ("数据治理", "数据质量", "数据管理", "元数据", "数据标准")),
    ("数据产品", ("数据产品", "产品经理")),
)
DEFAULT_MAJOR = "其他"

#: Boss 的学历取值 → 分析契约里的学历取值（其余一律归「不限」）
EDUCATION_MAP = {
    "大专": "大专",
    "本科": "本科",
    "硕士": "硕士",
    "博士": "博士",
    "学历不限": "不限",
    "不限": "不限",
    "中专/中技": "不限",
    "中专": "不限",
    "中技": "不限",
    "高中": "不限",
    "初中及以下": "不限",
}
DEFAULT_EDUCATION = "不限"

#: 岗位类型（映射到「毕业去向」）
JOB_TYPE_INTERN = "实习"
JOB_TYPE_CAMPUS = "校招"
JOB_TYPE_FULLTIME = "社招"
INTERN_KEYWORDS = ("实习", "intern", "Intern", "INTERN")
CAMPUS_KEYWORDS = ("应届", "在校", "校园", "毕业生", "管培", "储备干部")

#: 薪资解析常量
DAYS_PER_MONTH = 21.75  # 制度工作日（平均）
HOURS_PER_DAY = 8

#: 岗位吸引力代理分的权重（公开、可调；改动要同步 docs/metrics.md）
PROXY_BASE = 2.0
PROXY_WELFARE_HIT = 0.25
PROXY_WELFARE_EXTRA = 0.15
PROXY_WELFARE_CAP = 0.6
PROXY_FINANCE_BONUS = 0.4
PROXY_SCALE_BONUS = 0.3
PROXY_SCORE_CAP = 5.0
GOOD_FINANCE = ("已上市", "D轮及以上", "C轮", "B轮", "不需要融资")
GOOD_SCALE = ("1000-9999人", "10000人以上")
STABLE_WELFARE = ("五险一金", "双休", "周末双休", "年终奖", "补充医疗", "定期体检")

#: 分析契约 7 列（与 config.REQUIRED_COLUMNS 保持一致）
CANONICAL_COLUMNS: list[str] = list(config.REQUIRED_COLUMNS)

#: 岗位侧扩展列（不进管线，但保留给 SQL 层与 BI 切片用）
EXTRA_COLUMNS: list[str] = [
    "job_id",
    "job_title",
    "company",
    "industry",
    "finance_stage",
    "company_size",
    "experience_required",
    "job_type",
    "salary_lower",
    "salary_upper",
    "salary_months",
    "salary_unit",
    "city_district",
    "business_district",
    "welfare",
    "skills",
    "source",
    "crawled_at",
]


# =============================================================================
# 薪资解析
# =============================================================================
@dataclass(frozen=True)
class SalaryRange:
    """一条薪资字符串的解析结果（全部是解析出来的，不做任何假设）。"""

    raw: str
    lower: float | None  # 月薪下界（元）
    upper: float | None  # 月薪上界（元）
    months: int  # 年薪月数（「·13薪」→13；默认 12）
    unit: str  # month / day / hour（原始标注的时间单位）

    @property
    def monthly_mid(self) -> float | None:
        """区间中值（元/月）。只有单边时取下界。"""
        if self.lower is None and self.upper is None:
            return None
        if self.lower is None:
            return self.upper
        if self.upper is None:
            return self.lower
        return round((self.lower + self.upper) / 2, 2)

    @property
    def annual_mid(self) -> float | None:
        """年包中值 = 月薪中值 × 月数（「15-25K·13薪」→ 月 20000、年 260000）。"""
        monthly = self.monthly_mid
        return round(monthly * self.months, 2) if monthly is not None else None


_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(k|K|千|万|元)?")
_UNIT_VALUE = {"k": 1000, "K": 1000, "千": 1000, "万": 10000, "元": 1}


def parse_salary(text: Any) -> SalaryRange:
    """解析 Boss 的薪资字符串。

    覆盖的真实形态（见 tests/test_source_boss.py）：
        "15-25K·13薪" → 月 15000~25000、13 薪
        "8千-1.2万"   → 混合单位，按每个数字的相邻单位换算
        "1.5-2万"     → 15000~20000
        "200-300元/天"→ 日薪，按 21.75 天折算月薪
        "15K以上"     → 只有下界
        "面议" / 空串 → 无法解析（lower/upper 都是 None，不做任何猜测）
    """
    raw = "" if text is None else str(text).strip()
    if raw in {"", "面议", "薪资面议", "None", "nan", "null"}:
        return SalaryRange(raw=raw, lower=None, upper=None, months=12, unit="month")

    # 年薪月数：`·13薪` / `x14薪` / `*15薪`
    months = 12
    month_hit = re.search(r"[·xX*×]?\s*(\d{1,2})\s*薪", raw)
    if month_hit:
        value = int(month_hit.group(1))
        if 12 <= value <= 24:  # 12~24 薪才认，避免把「3薪」这类噪声当成年薪月数
            months = value

    # 时间单位：日薪 / 时薪 / 月薪
    unit = "month"
    if "元/天" in raw or "/天" in raw or "每天" in raw:
        unit = "day"
    elif "元/时" in raw or "/时" in raw or "/小时" in raw or "小时" in raw:
        unit = "hour"

    # 只取区间部分（去掉「·13薪」这类尾巴）
    body = raw.split("·")[0].split("薪")[0]

    tokens = _NUM_UNIT.findall(body)
    if not tokens:
        return SalaryRange(raw=raw, lower=None, upper=None, months=months, unit=unit)

    # 逐个数字配单位：没有自带单位时沿用最近一次出现的单位；都没有则按「元」处理
    values: list[float] = []
    last_unit = "元"
    for number, suffix in tokens:
        if suffix:
            last_unit = suffix
        values.append(float(number) * _UNIT_VALUE.get(last_unit, 1))

    # 日薪 / 时薪 → 折算月薪（口径写进 docs/data_source_boss.md）
    if unit == "day":
        values = [round(v * DAYS_PER_MONTH, 2) for v in values]
    elif unit == "hour":
        values = [round(v * HOURS_PER_DAY * DAYS_PER_MONTH, 2) for v in values]

    if "以上" in raw:
        lower, upper = values[0], None
    elif "以下" in raw:
        lower, upper = None, values[0]
    elif len(values) >= 2:
        lower, upper = sorted(values)[0], sorted(values)[-1]
    else:
        lower = upper = values[0]

    return SalaryRange(raw=raw, lower=lower, upper=upper, months=months, unit=unit)


# =============================================================================
# 字段映射
# =============================================================================
def classify_major(job_title: Any) -> str:
    """岗位标题 → 岗位方向（分析契约里「专业」列在岗位口径下的含义）。"""
    title = "" if job_title is None else str(job_title)
    for direction, keywords in MAJOR_RULES:
        if any(keyword in title for keyword in keywords):
            return direction
    return DEFAULT_MAJOR


def classify_job_type(job_title: Any, experience: Any) -> str:
    """岗位标题 + 经验要求 → 岗位类型（实习 / 校招 / 社招）。"""
    text = f"{job_title or ''} {experience or ''}"
    if any(keyword in text for keyword in INTERN_KEYWORDS):
        return JOB_TYPE_INTERN
    if any(keyword in text for keyword in CAMPUS_KEYWORDS):
        return JOB_TYPE_CAMPUS
    return JOB_TYPE_FULLTIME


def _welfare_list(record: dict[str, Any]) -> list[str]:
    welfare = record.get("welfareList") or record.get("welfare") or []
    if isinstance(welfare, str):
        welfare = re.split(r"[,，、/\s]+", welfare)
    return [str(item).strip() for item in welfare if str(item).strip()]


def attractiveness_score(
    welfare: Sequence[str],
    finance_stage: Any,
    company_size: Any,
) -> float:
    """岗位吸引力代理分（0~5，**proxy**，不是满意度）。

    公式公开、权重可调（常量在本文件顶部）：
        基础分 2.0
        + 每命中一个「稳定福利」0.25（最多计 3 个），其余福利每个 0.15（最多 4 个）
        + 融资阶段属于 GOOD_FINANCE +0.4
        + 公司规模属于 GOOD_SCALE +0.3
        截断到 [0, 5]，保留 1 位小数

    它衡量的是「招聘信息本身透露出的岗位质量信号」，
    **不能**当成员工满意度，也不能与问卷口径混用（见 docs/metrics.md）。
    """
    score = PROXY_BASE
    hits = [item for item in welfare if item in STABLE_WELFARE]
    score += min(len(hits), 3) * PROXY_WELFARE_HIT
    others = [item for item in welfare if item not in STABLE_WELFARE]
    score += min(len(others), 4) * PROXY_WELFARE_EXTRA
    score += PROXY_FINANCE_BONUS if str(finance_stage or "") in GOOD_FINANCE else 0.0
    score += PROXY_SCALE_BONUS if str(company_size or "") in GOOD_SCALE else 0.0
    return round(min(max(score, 0.0), PROXY_SCORE_CAP), 1)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def map_record(record: dict[str, Any], crawled_at: str | None = None) -> dict[str, Any]:
    """把一条 Boss 岗位原始记录映射成「7 列契约 + 扩展列」的一行。"""
    job = record.get("job", record) if isinstance(record, dict) else {}
    salary = parse_salary(job.get("salaryDesc") or job.get("salary"))
    job_type = classify_job_type(job.get("jobName"), job.get("jobExperience"))
    welfare = _welfare_list(job)

    row: dict[str, Any] = {
        # ---- 7 列分析契约（岗位口径）----
        config.COL_EDU: EDUCATION_MAP.get(_text(job.get("jobDegree")), DEFAULT_EDUCATION),
        config.COL_MAJOR: classify_major(job.get("jobName")),
        config.COL_SALARY: salary.monthly_mid,
        config.COL_SATISFACTION: attractiveness_score(
            welfare, job.get("brandStageName"), job.get("brandScaleName")
        ),
        config.COL_INTERN: "是" if job_type != JOB_TYPE_FULLTIME else "否",
        config.COL_DIRECTION: job_type,
        config.COL_CITY: _text(job.get("cityName"), "无"),
        # ---- 岗位侧扩展列 ----
        "job_id": _text(job.get("encryptJobId") or job.get("securityId") or job.get("jobId")),
        "job_title": _text(job.get("jobName")),
        "company": _text(job.get("brandName")),
        "industry": _text(job.get("brandIndustry")),
        "finance_stage": _text(job.get("brandStageName")),
        "company_size": _text(job.get("brandScaleName")),
        "experience_required": _text(job.get("jobExperience")),
        "job_type": job_type,
        "salary_lower": salary.lower,
        "salary_upper": salary.upper,
        "salary_months": salary.months,
        "salary_unit": salary.unit,
        "city_district": _text(job.get("areaDistrict")),
        "business_district": _text(job.get("businessDistrict")),
        "welfare": "|".join(welfare),
        "skills": "|".join(_text(item) for item in (job.get("skills") or []) if _text(item)),
        "source": "boss",
        "crawled_at": crawled_at or _text(record.get("crawled_at")),
    }
    return row


# =============================================================================
# 读写
# =============================================================================
def iter_raw_records(path: str | Path) -> Iterable[dict[str, Any]]:
    """逐行读原始 JSONL（容忍「裸 job 对象」与「带 meta 的包装」两种写法）。"""
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:  # pragma: no cover - 数据文件损坏时才触发
                raise ValueError(f"{path} 第 {line_no} 行不是合法 JSON：{exc}") from exc


def build_frame(
    raw_path: str | Path,
    *,
    proxy_satisfaction: bool = True,
    dedup: bool = True,
) -> pd.DataFrame:
    """原始 JSONL → analysis-ready DataFrame（7 列契约在前，扩展列在后）。

    Args:
        raw_path: 原始 JSONL 路径
        proxy_satisfaction: 是否用「岗位吸引力代理分」填满意度列；
            关掉时该列置空（交给清洗层按中位数填充，并在报告中显示为缺失）
        dedup: 是否按 job_id 去重（默认开；保留首次出现）
    """
    rows = [map_record(record) for record in iter_raw_records(raw_path)]
    if not rows:
        raise ValueError(f"{raw_path} 里没有任何记录")

    frame = pd.DataFrame(rows)
    if not proxy_satisfaction:
        frame[config.COL_SATISFACTION] = float("nan")
    if dedup and "job_id" in frame.columns:
        frame = frame.drop_duplicates(subset=["job_id"], keep="first")

    frame = frame.reset_index(drop=True)
    ordered = [column for column in CANONICAL_COLUMNS + EXTRA_COLUMNS if column in frame.columns]
    return frame.reindex(columns=ordered)


def save_analysis_csv(frame: pd.DataFrame, out_path: str | Path) -> Path:
    """写 analysis-ready CSV（utf-8-sig，Excel 直接双击不乱码）。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def build_dataset(raw_path: str | Path, out_path: str | Path, *, dedup: bool = True) -> pd.DataFrame:
    """一步到位：原始 JSONL → 分析 CSV。返回写入的 DataFrame。"""
    frame = build_frame(raw_path, dedup=dedup)
    save_analysis_csv(frame, out_path)
    return frame


# =============================================================================
# 命令行：python -m src.sources.boss --raw <raw.jsonl> --out <dataset.csv>
# =============================================================================
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Boss 直聘原始 JSONL 转成项目分析口径的 CSV（不做抓取、不做统计）",
    )
    parser.add_argument("--raw", default="data/raw/boss/raw_jobs.jsonl", help="原始 JSONL 路径")
    parser.add_argument("--out", default=str(config.BOSS_DATA_PATH), help="输出 CSV 路径")
    parser.add_argument("--no-dedup", action="store_true", help="不按 job_id 去重")
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="不生成岗位吸引力代理分（满意度列留空，交给清洗层填充）",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass

    raw_path = Path(args.raw)
    if not raw_path.exists():
        print(f"[错误] 找不到原始数据：{raw_path}")
        print("       先跑 python scripts/crawl_boss.py 抓取，或用仓库自带的样例：")
        print("       python -m src.sources.boss --raw data/raw/sample/boss_jobs_sample.jsonl "
              f"--out {args.out}")
        return 2

    try:
        frame = build_frame(raw_path, proxy_satisfaction=not args.no_proxy, dedup=not args.no_dedup)
    except ValueError as exc:
        print(f"[错误] {exc}")
        return 2

    path = save_analysis_csv(frame, args.out)
    print(f"[OK] 已生成分析数据集：{path}")
    print(f"     记录数：{len(frame)}｜列数：{frame.shape[1]}｜抓取时间：{datetime.now():%Y-%m-%d %H:%M}")
    print("     列（前 7 列是分析契约，其余是岗位侧扩展列）：")
    print("       " + "、".join(frame.columns))
    print("\n下一步：")
    print(f"       python main.py --data \"{path}\"          # 跑完整分析（统计/体检/报告/图表）")
    print("       python scripts/load_to_mysql.py --backend sqlite   # 装载进 SQL 层并跑门禁")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())