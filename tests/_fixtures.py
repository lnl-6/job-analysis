# -*- coding: utf-8 -*-
"""测试用的岗位记录夹具：仓库不携带任何数据集，所有测试数据都由这里现场生成。

为什么要有这个文件：仓库里只入库「聚合统计」（data/published/），原始抓取数据与
岗位级数据集都不入库。测试仍然需要数据，于是统一在这里造——覆盖解析边界
（K / 千 / 万 / 元·天 / 面议、13 薪、实习·校招·社招、6 个岗位大类、缺失字段、
一条重复 job_id 用于去重断言），并且不依赖网络、Cookie 或仓库里的文件。

用法：
    from _fixtures import write_records
    path = write_records(Path(tmp) / "jobs.jsonl")

命令行（CI 里生成一份临时数据集用）：
    python tests/_fixtures.py --jsonl /tmp/boss_fixture.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# 一条记录 = Boss 搜索接口返回的「job」对象的子集；外层再包一层 meta，
# 与 scripts/crawl_boss.py 落盘的 JSONL 结构保持一致。
# 字段顺序：job_id, 标题, 薪资, 学历, 经验, 公司, 行业, 融资阶段, 规模, 城市, 区,
#           福利, 技能, 发布时间, 是否带 JD
_RAW: tuple[tuple[Any, ...], ...] = (
    ("job-001", "数据分析师", "15-25K·13薪", "本科", "3-5年", "甲数据科技", "互联网", "B轮", "100-499人", "北京", "海淀区", ["五险一金", "双休", "年终奖"], ["Python", "SQL", "Tableau"], "2026-11-10", True),
    ("job-001", "数据分析师", "15-25K·13薪", "本科", "3-5年", "甲数据科技", "互联网", "B轮", "100-499人", "北京", "海淀区", ["五险一金", "双休", "年终奖"], ["Python", "SQL", "Tableau"], "2026-11-10", True),
    ("job-002", "数据分析师", "1.2-1.8万", "本科", "1-3年", "乙电商", "电子商务", "已上市", "10000人以上", "上海", "浦东新区", ["五险一金", "弹性工作"], ["SQL", "Excel", "PowerBI"], "2026-11-09", False),
    ("job-003", "数据开发工程师", "20-35K", "本科", "3-5年", "丙金融", "银行", "不需要融资", "1000-9999人", "北京", "朝阳区", ["六险一金", "补充医疗", "股票期权"], ["Java", "Hive", "Spark"], "2026-11-08", False),
    ("job-004", "数据开发工程师", "18-28K·14薪", "硕士", "3-5年", "丁物流", "物流运输", "C轮", "500-999人", "上海", "青浦区", ["五险一金", "餐补"], ["Python", "Flink", "Kafka"], "2026-11-07", False),
    ("job-005", "算法工程师（推荐）", "30-50K", "硕士", "5-10年", "戊科技", "人工智能", "D轮及以上", "100-499人", "杭州", "余杭区", ["股票期权", "免费班车", "补充医疗"], ["Python", "TensorFlow", "PyTorch"], "2026-11-06", True),
    ("job-006", "数据产品经理", "20-30K", "本科", "3-5年", "己零售", "新零售", "B轮", "500-999人", "深圳", "南山区", ["五险一金", "年终奖"], ["SQL", "Axure", "数据建模"], "2026-11-06", False),
    ("job-007", "商业分析师", "12-18K", "本科", "1-3年", "庚咨询", "企业服务", "A轮", "100-499人", "成都", "武侯区", ["五险一金", "培训"], ["SQL", "Excel", "Python"], "2026-11-05", False),
    ("job-008", "数据仓库工程师", "22-32K", "本科", "5-10年", "辛银行", "银行", "已上市", "10000人以上", "武汉", "江汉区", ["六险一金", "年终奖", "补充医疗"], ["Hive", "Spark", "数仓建模"], "2026-11-04", False),
    ("job-009", "数据挖掘工程师", "25-40K", "硕士", "3-5年", "壬保险", "保险", "不需要融资", "10000人以上", "南京", "建邺区", ["五险一金", "补充医疗"], ["Python", "XGBoost", "SQL"], "2026-11-04", False),
    ("job-010", "数据运营专员", "8-12K", "大专", "1-3年", "癸传媒", "广告营销", "不需要融资", "100-499人", "西安", "雁塔区", ["五险一金", "双休"], ["Excel", "SQL"], "2026-11-03", False),
    ("job-011", "数据分析实习生", "300-400元/天", "本科", "在校/应届", "甲数据科技", "互联网", "B轮", "100-499人", "上海", "徐汇区", ["转正机会", "导师带教"], ["SQL", "Python"], "2026-11-03", False),
    ("job-012", "数据治理工程师", "15-22K", "本科", "1-3年", "子国企", "政府/公共事业", "不需要融资", "10000人以上", "苏州", "工业园区", ["五险一金", "年终奖"], ["SQL", "数据标准"], "2026-11-02", False),
    ("job-013", "数据分析师（校招）", "10-14K", "本科", "在校/应届", "丑快消", "快速消费品", "已上市", "10000人以上", "广州", "天河区", ["五险一金", "培训", "年终奖"], ["SQL", "Excel", "Python"], "2026-11-01", False),
    ("job-014", "资深数据科学家", "面议", "博士", "10年以上", "寅研究院", "学术/科研", "不需要融资", "20-99人", "北京", "西城区", ["科研经费"], ["R", "Python", "统计建模"], "2026-10-30", False),
    ("job-015", "BI 工程师", "9千-1.4万", "本科", "1-3年", "卯制造", "机械设备", "未融资", "1000-9999人", "深圳", "宝安区", ["五险一金", "住宿"], ["PowerBI", "SQL", "Excel"], "2026-10-29", False),
    ("job-016", "数据分析师", "13-20K", "本科", "3-5年", "辰汽车", "汽车", "已上市", "10000人以上", "合肥", "蜀山区", ["五险一金", "年终奖", "补充医疗"], ["SQL", "Python", "Hive"], "2026-10-28", False),
    ("job-017", "数据开发工程师", "16-24K", "本科", "3-5年", "巳家电", "家电制造", "已上市", "10000人以上", "佛山", "顺德区", ["五险一金", "住房补贴"], ["Spark", "SQL", "Airflow"], "2026-10-27", False),
    ("job-018", "数据分析师（电商）", "14-22K", "本科", "1-3年", "午跨境", "电子商务", "B轮", "500-999人", "东莞", "松山湖", ["五险一金", "双休", "下午茶"], ["SQL", "Excel", "Tableau"], "2026-10-26", False),
    ("job-019", "数据运营（实习）", "200-300元/天", "本科", "在校/应届", "未物流", "物流运输", "C轮", "1000-9999人", "宁波", "鄞州区", ["转正机会", "免费班车"], ["Excel", "SQL"], "2026-10-25", False),
    ("job-020", "数据治理实习生", "150-250元/天", "硕士", "在校/应届", "申研究院", "学术/科研", "不需要融资", "20-99人", "北京", "海淀区", ["导师带教"], ["Python", "数据质量"], "2026-10-25", True),
    ("job-021", "数据产品经理（BI）", "25-35K·13薪", "本科", "3-5年", "酉互联网", "互联网", "D轮及以上", "1000-9999人", "杭州", "滨江区", ["五险一金", "股票期权", "弹性工作"], ["SQL", "PowerBI", "指标体系"], "2026-10-24", False),
    ("job-022", "数据分析主管", "30-45K", "本科", "5-10年", "戌地产", "房地产", "已上市", "1000-9999人", "广州", "海珠区", ["五险一金", "年终奖"], ["SQL", "Python", "团队管理"], "2026-10-23", False),
)


def sample_records() -> list[dict[str, Any]]:
    """返回岗位原始记录列表（结构同 crawl_boss 落盘的 JSONL 每一行）。"""
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(_RAW, start=1):
        (
            job_id,
            title,
            salary,
            degree,
            experience,
            company,
            industry,
            stage,
            scale,
            city,
            district,
            welfare,
            skills,
            publish,
            with_jd,
        ) = raw
        job: dict[str, Any] = {
            "encryptJobId": job_id,
            "jobName": title,
            "salaryDesc": salary,
            "jobDegree": degree,
            "jobExperience": experience,
            "brandName": company,
            "brandIndustry": industry,
            "brandStageName": stage,
            "brandScaleName": scale,
            "cityName": city,
            "areaDistrict": district,
            "businessDistrict": "",
            "welfareList": list(welfare),
            "skills": list(skills),
            "jobTime": publish,
        }
        if with_jd:
            job["jobDescription"] = f"负责数据指标体系与报表建设，参与业务分析，第 {index} 号岗位。"
            job["_detail_status"] = "ok"
        records.append({"crawled_at": "2026-11-14 12:00:00", "job": job})
    return records


def write_records(path: str | Path, records: list[dict[str, Any]] | None = None) -> Path:
    """把记录写成 JSONL 文件并返回路径（默认写 sample_records）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = records if records is not None else sample_records()
    with target.open("w", encoding="utf-8") as handle:
        for record in payload:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return target


def main(argv: list[str] | None = None) -> int:
    """CLI：生成一份夹具 JSONL（CI 里给端到端步骤用）。"""
    import argparse

    parser = argparse.ArgumentParser(description="生成测试用岗位数据（JSONL）")
    parser.add_argument("--jsonl", required=True, help="输出文件路径")
    args = parser.parse_args(argv)

    path = write_records(args.jsonl)
    print(f"[夹具] 已写入 {path}（{len(sample_records())} 条记录，非真实抓取数据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())