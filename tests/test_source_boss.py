# -*- coding: utf-8 -*-
"""Boss 直聘数据源适配层测试：薪资解析 / 字段映射 / 契约完整 / 端到端可跑。

全部离线：只用 `data/raw/sample/boss_jobs_sample.jsonl`（手工样例，非真实抓取），
所以这些测试在 CI 里不需要 Cookie、不需要网络。

跑法（两种等价）：
    python tests/test_source_boss.py
    python -m pytest -q tests/
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import crawl_boss  # noqa: E402
from src import analysis, cleaner, config, loader, report  # noqa: E402
from src.sources import boss  # noqa: E402

SAMPLE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "sample" / "boss_jobs_sample.jsonl"
)


# ---------------------------------------------------------------------------
# 薪资解析
# ---------------------------------------------------------------------------
def test_parse_salary_monthly_with_months() -> None:
    """`15-25K·13薪`：月薪区间 + 年薪月数都要解析出来。"""
    salary = boss.parse_salary("15-25K·13薪")
    assert (salary.lower, salary.upper) == (15000.0, 25000.0)
    assert salary.months == 13
    assert salary.monthly_mid == 20000.0
    assert salary.annual_mid == 260000.0


def test_parse_salary_mixed_units() -> None:
    """混合单位：每个数字配它自己的单位，不能拿尾部单位一刀切。"""
    salary = boss.parse_salary("8千-1.2万")
    assert (salary.lower, salary.upper) == (8000.0, 12000.0)


def test_parse_salary_daily_rate() -> None:
    """日薪要折算成月薪（21.75 天），并在 unit 上留痕。"""
    salary = boss.parse_salary("200-300元/天")
    assert salary.unit == "day"
    assert salary.lower == round(200 * boss.DAYS_PER_MONTH, 2)
    assert salary.upper == round(300 * boss.DAYS_PER_MONTH, 2)


def test_parse_salary_unparsable_and_single_side() -> None:
    """面议/空串不能瞎猜；`15K以上` 只给下界。"""
    for raw in ("面议", "", None, "薪资面议"):
        salary = boss.parse_salary(raw)
        assert salary.monthly_mid is None, f"{raw!r} 不该解析出金额"
    above = boss.parse_salary("15K以上")
    assert above.lower == 15000.0 and above.upper is None


# ---------------------------------------------------------------------------
# 字段映射
# ---------------------------------------------------------------------------
def test_classify_major() -> None:
    """岗位标题 → 岗位方向（分析契约里「专业」列在岗位口径下的含义）。"""
    cases = {
        "数据分析师": "数据分析",
        "商业分析师": "数据分析",
        "数据运营": "数据分析",
        "数据开发工程师": "数据开发",
        "数仓开发": "数据开发",
        "算法工程师": "算法",
        "大模型算法工程师": "算法",
        "数据挖掘工程师": "算法",
        "数据治理专员": "数据治理",
        "数据产品经理": "数据产品",
        "行政专员": boss.DEFAULT_MAJOR,
    }
    for title, expected in cases.items():
        assert boss.classify_major(title) == expected, f"{title} → {boss.classify_major(title)}"


def test_attractiveness_score_bounds_and_order() -> None:
    """代理分必须在 0~5 之间，且「福利多 + 融资好 + 规模大」应高于「福利少 + 天使轮 + 小公司」。"""
    rich = boss.attractiveness_score(
        ["五险一金", "双休", "年终奖", "补充医疗", "定期体检"],
        "已上市",
        "10000人以上",
    )
    poor = boss.attractiveness_score(["零食下午茶"], "天使轮", "20-99人")
    assert 0.0 <= poor < rich <= 5.0


def test_map_record_contract() -> None:
    """一条岗位记录映射后：7 列契约齐全，岗位类型与学历映射正确。"""
    record = {
        "job": {
            "encryptJobId": "x1",
            "jobName": "数据分析实习生",
            "salaryDesc": "200-300元/天",
            "jobDegree": "学历不限",
            "jobExperience": "在校/应届",
            "cityName": "北京",
            "brandStageName": "已上市",
            "brandScaleName": "1000-9999人",
            "welfareList": ["双休"],
        }
    }
    row = boss.map_record(record)
    for column in boss.CANONICAL_COLUMNS:
        assert column in row, f"缺少分析契约列：{column}"
    assert row[config.COL_EDU] == "不限"
    assert row[config.COL_DIRECTION] == boss.JOB_TYPE_INTERN
    assert row[config.COL_INTERN] == "是"
    assert row[config.COL_MAJOR] == "数据分析"
    assert row["job_type"] == boss.JOB_TYPE_INTERN


def test_build_frame_dedups_by_job_id() -> None:
    """样例里有一行重复岗位，按 job_id 去重后应少一行。"""
    frame = boss.build_frame(SAMPLE_PATH)
    raw_lines = sum(1 for line in SAMPLE_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
    assert len(frame) == raw_lines - 1, f"去重后行数不对：{len(frame)} vs {raw_lines}"
    assert frame["job_id"].is_unique


# ---------------------------------------------------------------------------
# 端到端：样例 → CSV → 既有分析流水线
# ---------------------------------------------------------------------------
def test_sample_dataset_runs_through_pipeline() -> None:
    """岗位样例转成 CSV 后，必须能被 loader/cleaner/analysis 原样吃下。"""
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "boss_岗位数据集.csv"
        frame = boss.build_dataset(SAMPLE_PATH, out_path)
        assert frame[config.COL_SALARY].notna().any(), "至少要有可解析的薪资"

        df, info = loader.load_data(out_path)
        assert info.rows == len(frame)
        cleaned, cleaning = cleaner.clean(df)
        assert len(cleaned) > 0

        results = analysis.run_all(cleaned, top_n=5)
        for key in ("ov", "edu", "major_top", "city", "comparisons", "quality", "group_stats"):
            assert key in results, f"分析结果缺 key：{key}"

        # 报告必须把「这批数字是什么口径」写在溯源里 —— 换数据源最容易被误读的地方
        note = config.source_note_for(info.path.name)
        assert note is not None and note[0] == "Boss直聘岗位数据"
        provenance = "\n".join(report.provenance_lines(info, results))
        assert "数据口径" in provenance and "岗位薪资区间中值" in provenance


# ---------------------------------------------------------------------------
# 抓取侧的缓存（断点续爬）不联网也能测
# ---------------------------------------------------------------------------
def test_crawl_cache_roundtrip() -> None:
    """append_record 写入的记录应能被 load_cache 读回，并正确识别已抓页面与 job id。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raw.jsonl"
        payload = {
            "query": "数据分析",
            "city_code": "101010100",
            "page": 1,
            "crawled_at": "2026-11-09 10:00:00",
            "job": {"encryptJobId": "cache-1", "jobName": "数据分析师"},
        }
        crawl_boss.append_record(path, payload)
        crawl_boss.append_record(path, payload)  # 重复写一行，job id 去重靠调用方

        pages, job_ids, records = crawl_boss.load_cache(path)
        # 多轮采集下「这页抓过没有」必须带轮次：(round, query, city_code, page)
        assert (1, "数据分析", "101010100", 1) in pages
        assert job_ids == {"cache-1"}
        assert len(records) == 2
        assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["job"]["encryptJobId"] == "cache-1"


# ---------------------------------------------------------------------------
# 序数化 / 薪资带宽 / 发布时间 / 多值字段 / 详情页
# ---------------------------------------------------------------------------
def test_experience_and_size_rank() -> None:
    """经验与规模都要映射成可排序的序数；「不限」和未知档位给 9，不能悄悄变成 0。"""
    assert boss.experience_rank("在校/应届") == 0
    assert boss.experience_rank("3-5年") == 3
    assert boss.experience_rank("10年以上") == 5
    assert boss.experience_rank("经验不限") == 9
    assert boss.experience_rank(None) == 9
    assert boss.experience_rank("没出现过的新档位") == 9

    assert boss.company_size_rank("0-20人") == 0
    assert boss.company_size_rank("100-499人") == 2
    assert boss.company_size_rank("1000-9999人") == 4
    assert boss.company_size_rank("10000人以上") == 5
    assert boss.company_size_rank("") == 9


def test_salary_band_ratio() -> None:
    """薪资带宽 =（上界-下界）/中值；单边或解析失败时没有带宽。"""
    assert boss.salary_band_ratio(boss.parse_salary("15-25K")) == 0.5
    assert boss.salary_band_ratio(boss.parse_salary("20K以上")) is None
    assert boss.salary_band_ratio(boss.parse_salary("面议")) is None


def test_parse_publish_date_forms() -> None:
    """发布时间：时间戳 / 标准日期 / 相对时间都能归一；推不出来就返回 None（不猜）。"""
    from datetime import datetime, timedelta

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    stamp = datetime.fromtimestamp(1_700_000_000)

    assert boss.parse_publish_date("2025-05-23") == "2025-05-23"
    assert boss.parse_publish_date("2025/5/23") == "2025-05-23"
    assert boss.parse_publish_date("1700000000000") == stamp.strftime("%Y-%m-%d")
    assert boss.parse_publish_date("1700000000") == stamp.strftime("%Y-%m-%d")
    assert boss.parse_publish_date("今天") == today
    assert boss.parse_publish_date("昨天") == (now - timedelta(days=1)).strftime("%Y-%m-%d")
    assert boss.parse_publish_date(f"发布于 {now.month}月{now.day}日") == today
    assert boss.parse_publish_date("三周前") is None
    assert boss.parse_publish_date(None) is None


def test_split_multi_values() -> None:
    """多值字段先规范成「|」拼接，再拆成列表（去空、去重、保序）。"""
    assert boss.split_multi("五险一金|双休|年终奖") == ["五险一金", "双休", "年终奖"]
    assert boss.split_multi("Python，SQL、Python") == ["Python", "SQL"]
    assert boss.split_multi("") == []
    assert boss.split_multi(None) == []


def test_map_record_fills_job_market_fields() -> None:
    """适配层必须把库与分析要用的扩展列都填上，且列名覆盖 EXTRA_COLUMNS。"""
    record = {
        "crawled_at": "2026-11-09 10:00:00",
        "job": {
            "encryptJobId": "job-x",
            "jobName": "数据分析师",
            "salaryDesc": "15-25K·13薪",
            "jobDegree": "本科",
            "jobExperience": "3-5年",
            "brandName": "某公司",
            "brandIndustry": "互联网",
            "brandStageName": "B轮",
            "brandScaleName": "100-499人",
            "cityName": "广州",
            "welfareList": ["五险一金", "双休"],
            "skills": ["Python", "SQL"],
            "jobTime": "2026-11-01",
        },
    }
    row = boss.map_record(record)

    assert set(boss.EXTRA_COLUMNS) <= set(row)
    assert row["experience_rank"] == 3
    assert row["company_size_rank"] == 2
    assert row["salary_band_ratio"] == 0.5
    assert row["publish_date"] == "2026-11-01"
    assert row["detail_status"] == "none"
    assert row["skills"] == "Python|SQL"
    assert row["jd_len"] == 0


def test_parse_detail_payload_json_and_html() -> None:
    """详情解析：接口 JSON 与页面 HTML 两条路径都要能取出 JD；取不到给 empty。"""
    payload = {"zpData": {"jobInfo": {"jobDescription": "负责报表与指标体系建设。"}}}
    assert crawl_boss.parse_detail_payload(payload) == ("负责报表与指标体系建设。", "ok")

    html = '<div class="job-sec-text">负责<b>数据分析</b>与报表</div>'
    text, status = crawl_boss.parse_detail_payload(html)
    assert status == "ok"
    assert "数据分析" in text
    assert "<b>" not in text

    assert crawl_boss.parse_detail_payload({"zpData": {}}) == ("", "empty")
    assert crawl_boss.parse_detail_payload('<div class="x">没有 JD</div>') == ("", "empty")


def test_crawl_offline_writes_manifest() -> None:
    """--offline 也要写采集清单：报告溯源与复现都要靠它。"""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.jsonl"
        manifest = Path(tmp) / "manifest.json"
        args = crawl_boss.parse_args(["--offline", "--out", str(raw), "--manifest", str(manifest)])
        assert crawl_boss.crawl(args) == 0

        assert manifest.exists()
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert payload["source"] == "boss"
        assert payload["exit_code"] == 0
        assert set(crawl_boss.STAT_KEYS) <= set(payload["stats"])
        assert payload["totals"]["records"] == 0


def test_default_source_prefers_boss_dataset() -> None:
    """默认数据源必须是 Boss 抓取产物；问卷 CSV 不在候选里，只能 --data 显式指定。"""
    candidates = loader.default_data_candidates()
    assert candidates[0] == config.BOSS_DATA_PATH
    assert candidates[-1] == config.DEMO_DATA_PATH
    assert config.REAL_DATA_PATH not in candidates


def main() -> int:
    tests = [
        ("薪资：月薪 + 年薪月数", test_parse_salary_monthly_with_months),
        ("薪资：混合单位", test_parse_salary_mixed_units),
        ("薪资：日薪折算", test_parse_salary_daily_rate),
        ("薪资：面议与单边", test_parse_salary_unparsable_and_single_side),
        ("岗位标题 → 岗位方向", test_classify_major),
        ("吸引力代理分：范围与单调性", test_attractiveness_score_bounds_and_order),
        ("单条记录 → 7 列契约", test_map_record_contract),
        ("按 job_id 去重", test_build_frame_dedups_by_job_id),
        ("样例 → CSV → 分析流水线", test_sample_dataset_runs_through_pipeline),
        ("抓取缓存往返（断点续爬）", test_crawl_cache_roundtrip),
        ("经验 / 公司规模序数化", test_experience_and_size_rank),
        ("薪资带宽", test_salary_band_ratio),
        ("发布时间归一", test_parse_publish_date_forms),
        ("多值字段拆分", test_split_multi_values),
        ("扩展列填充（岗位市场字段）", test_map_record_fills_job_market_fields),
        ("详情页解析（JSON + HTML）", test_parse_detail_payload_json_and_html),
        ("离线抓取也写 manifest", test_crawl_offline_writes_manifest),
        ("默认数据源优先 Boss 产物", test_default_source_prefers_boss_dataset),
    ]

    failed = 0
    print("Boss 直聘数据源适配测试（全部离线）")
    print("-" * 60)
    for name, func in tests:
        try:
            func()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {name}：{exc}")
        except Exception as exc:  # noqa: BLE001 - 运行器要报告任何异常
            failed += 1
            print(f"[ERROR] {name}：{type(exc).__name__}: {exc}")
        else:
            print(f"[OK]   {name}")
    print("-" * 60)
    print(f"{len(tests) - failed} / {len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())