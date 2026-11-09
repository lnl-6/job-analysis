# sample/ —— 手工样例（不是真实抓取结果）

`boss_jobs_sample.jsonl` 是**手工编造的样例数据**，只为两件事存在：

1. 让你在没有 Cookie、没有网络的情况下，也能把「岗位数据 → 分析口径 → 报告」这条路跑通；
2. 给 `tests/test_source_boss.py` 提供确定性输入（薪资格式、学历取值、岗位方向、去重键都覆盖到了）。

每条记录里的 `"_sample": true` 就是提醒。

## 它覆盖了哪些真实形态

| 形态 | 样例里的例子 |
| --- | --- |
| 月薪区间 + 年薪月数 | `15-25K·13薪`、`25-35K·14薪` |
| 混合单位 | 无（真实数据里偶见，解析器已支持 `8千-1.2万`，见测试） |
| 日薪（实习） | `200-300元/天` |
| 面议 | `面议`（薪资列留空，交给清洗层按中位数填充并标记） |
| 学历取值 | 本科 / 硕士 / 博士 / 大专 / 学历不限 |
| 岗位类型 | 实习（在校/应届）、校招（应届生）、社招 |
| 重复岗位 | 最后一行与前文 `boss-sample-0001` 重复，用于验证按 job_id 去重 |

## 怎么用

```bash
# 转成分析口径的 CSV（默认写到 data/raw/boss_岗位数据集.csv）
python -m src.sources.boss --raw data/raw/sample/boss_jobs_sample.jsonl \
    --out "data/raw/boss_岗位数据集.csv"

# 跑完整分析
python main.py --data "data/raw/boss_岗位数据集.csv"
```

真实抓取请用 `python scripts/crawl_boss.py`（需要 Cookie，见脚本头部说明与 `docs/data_source_boss.md`）。
真实抓取产物在 `data/raw/boss/`，已在 `.gitignore` 里排除，不会进仓库。