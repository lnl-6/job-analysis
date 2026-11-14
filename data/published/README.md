# data/published —— 可公开入库的聚合统计

这个目录只放**聚合结果**，不放原始抓取记录。

| 文件 | 内容 | 生成方式 |
| --- | --- | --- |
| `boss_market_summary.csv` | Boss 岗位数据的维度统计（样本量 / 薪资分布 / 满意度 / 实习占比） | `python scripts/export_published_summary.py` |

## 为什么原始数据不在这里

原始抓取数据（`data/raw/boss/raw_jobs.jsonl`、`data/raw/boss_岗位数据集.csv`）在 `.gitignore` 里，原因是：

1. 内容属于站点，含公司名与单条岗位记录，不适合重新分发到公开仓库；
2. 体量会随抓取批次持续增长（当前 2.3 MB），一旦进入 Git 历史就无法回收；
3. 测试与 CI 只需要 `data/raw/sample/` 下的小样例，不依赖真实抓取数据。

仓库需要证明「分析确实跑在真实数据上」，所以发布这份聚合量：它保留了维度取值与统计量，
去掉了公司名、岗位标题、JD 文本、技能/福利等岗位级信息。

## `boss_market_summary.csv` 的列

| 列 | 含义 |
| --- | --- |
| `scope` | 统计口径：`overall` / `city` / `city_tier` / `major` / `education` / `direction` / `city_major` |
| `dimension` | 维度名（中文，与报告一致，例如「工作城市」） |
| `value` | 维度取值（`city_major` 形如 `数据分析@北京`） |
| `jobs` | 岗位数（清洗后，即报告里的样本量） |
| `salary_n` | 有薪资解析值的岗位数 |
| `mean_salary` / `median_salary` | 平均 / 中位期望薪资（元；岗位数据下为薪资区间中值） |
| `p25` / `p75` / `salary_std` | 四分位与标准差（元，线性插值口径） |
| `mean_satisfaction` | 平均满意度（0~5；Boss 数据源下是平台吸引力代理分） |
| `intern_share` | 实习岗占比（0~1） |
| `sample_ok` | 样本量是否达到门槛（`config.MIN_GROUP_SIZE`，默认 30）；小样本格子不发布 |

## 当前这份数据的来源

| 项 | 值 |
| --- | --- |
| 数据源 | 现场抓取 Boss 直聘「数据分析」搜索结果 |
| 采集日期 | 2026-11-14 |
| 原始记录 | 2021 条 × 47 个城市 |
| 清洗后 | 1864 条（无效薪资 0 条、IQR 异常 157 条） |
| 数据指纹 | `2e56b3254888`（SHA-256 前 12 位，与 `output/report.md` 一致） |

重新生成：

```bash
python scripts/export_published_summary.py
# 默认读取 data/raw/boss_岗位数据集.csv（缺失时回退到演示数据），输出到本目录
```

口径细节见 [`docs/metrics.md`](../../docs/metrics.md)，数据源说明见
[`docs/data_source_boss.md`](../../docs/data_source_boss.md)。