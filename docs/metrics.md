# 指标口径对照表

这个项目里每个对外数字都要能回答三个问题：**怎么算的、在哪算的、为什么这么算**。
本文件就是那份答案。

三条原则：

1. **统计量只算一次** —— 分析层（`src/analysis.py` + `src/stats.py`）算完写进结果表，
   SQL 层与 BI 只搬运和呈现，绝不在数据库或 DAX 里重算一遍对外数字。
2. **对外数字以 Python 侧为准** —— SQL 侧提供了一份独立实现用于交叉核对（见下），
   两边口径不同时以 Python 为准，并把差异写清楚。
3. **算不出来的就说算不出来** —— 样本不足、组内无变异、权重一改排序就翻，
   这些情况在报告和数据库里都如实写，不给「看起来像结论」的数字。

---

## 一、指标 → 出处对照（三处）

| 指标 | 定义 | Python 出处 | SQL 出处 |
| --- | --- | --- | --- |
| 平均期望薪资 | 分组内 `expected_salary` 的算术平均 | `src/analysis.py::group_stats` / 各维度分析函数 | `v_group_share.mean_salary`、`ads_group_stats.mean_salary` |
| 薪资中位数 | 50% 分位数（线性插值） | `src/stats.py::describe`、`quantiles` | `ads_group_stats.median_salary` |
| 分位数 P25/P50/P75 | 线性插值（numpy 默认口径） | `src/stats.py::quantiles` | `ads_group_stats.p25/p50/p75`（同源）；`v_salary_percentiles_nearest_rank` 为 nearest-rank 对照 |
| 均值置信区间 | t 分布，`CI_LEVEL = 0.95` | `src/stats.py::describe`（`均值置信区间下界/上界`） | `ads_group_stats.salary_ci_low/salary_ci_high` |
| 组间差异显著性 | Welch t 检验（不假设方差齐性） | `src/stats.py::compare` → `Comparison.p_welch` | `ads_comparisons.p_welch` |
| 稳健性（分布无关） | Mann-Whitney U 检验 | `Comparison.p_mannwhitney` | `ads_comparisons.p_mannwhitney` |
| 效应量 | Cohen's d（合并标准差） | `Comparison.cohens_d` + `effect_size_label` | `ads_comparisons.cohens_d/effect_label` |
| 多重比较校正 | Holm（家族内逐步校正） | `src/stats.py::holm_adjust` | `ads_comparisons.p_holm`（判定以它为准） |
| 判定文案 | 「差异显著 / 不显著 / 样本不足，不给结论 / 无法检验」 | `Comparison.verdict` | `ads_comparisons.verdict` |
| 满意度均值 | 分组内 `satisfaction` 的算术平均 | `src/analysis.py::group_stats` | `ads_group_stats.mean_satisfaction` |
| 实习率 | 组内「是否有实习经历 = 是」的占比（%） | `src/analysis.py::group_stats` | `ads_group_stats.internship_rate` |
| 城市权重敏感性 | 6 组权重下城市排序的 Spearman 秩相关、TOP1 是否变化 | `src/recommender.py::weight_sensitivity` | `ads_weight_sensitivity.*` |
| 数据指纹 | 数据文件 SHA-256 前 12 位 | `src/report.py::_file_sha256`、`src/db.py::_data_fingerprint` | `ads_run_meta.data_sha256`（全长）、`load_batch`（前 12 位） |
| 数据质量体检 | 缺失率、重复行、分组解释力（ANOVA + eta²）、分布形态、取值集中度 | `src/quality.py::audit` | `ads_quality_checks.*` |

---

## 二、容易吵架的几个口径

### 1. 分位数为什么有两套

- **Python 侧**：`numpy.quantile` 的默认线性插值（n 个样本、p 分位取相邻两点的插值）。
- **SQL 侧**：`nearest-rank` 定义（最小的秩 ≥ n·p 的那个实际观测值），只依赖整数运算，
  任何数据库都能跑，见 `sql/01_views.sql` 的 `v_salary_percentiles_nearest_rank`。

两者差的是「插值 vs 取实际值」，量级通常在几十元以内。
`sql/queries/02_salary_percentiles_cross_check.sql` 把两边并排打出来量化这个差异；
**对外（报告、BI）一律用 Python 侧的线性插值**，并在文档里写明。

### 2. 显著性怎么判

```
sample_ok   = 两组样本量都 >= config.MIN_GROUP_SIZE（30）
significant = sample_ok 且 p_holm < config.ALPHA（0.05）
```

- 用 **Holm 校正后的 p 值**（`p_holm`）判定，不用原始 p 值 —— 一次跑 5~6 组对比，
  不做校正就是在制造假阳性。
- **显著 ≠ 重要**：报告同时给 Cohen's d；`|d| < 0.2` 时标签是「可忽略」，
  即使 p 值很小也不建议当卖点讲。
- 两组样本量不足 30、或某一组内没有任何变异（常量），一律不给「显著 / 不显著」结论，
  判定文案直接写「样本不足，不给结论」或「无法检验（组内没有变异）」。

### 3. 假阳性怎么自查

`python scripts/generate_demo_data.py --scenario null` 生成一份**按定义不含任何分组差异**
的数据（所有维度的效应系数都设为 1.0），再跑同一条管线：
如果报出「差异显著」，那就是假阳性。测试用固定随机种子断言「一次都不该出现」
（`tests/test_pipeline.py::test_null_scenario_is_effect_free`），CI 也会跑一遍。

### 4. 缺失值与无效值

| 情况 | 处理 | 为什么 |
| --- | --- | --- |
| 期望薪资缺失 | 用**中位数**填充，并在 `dwd_survey_clean.salary_is_filled` 标记 | 薪资右偏，中位数抗异常值；标记保留可追溯性 |
| 期望薪资 ≤ 0 | **剔除**（`src/cleaner.py::drop_invalid_salary`），计入 `ads_run_meta.rows_invalid_salary` | 负薪资是无效记录，不是缺失值，填不了 |
| 满意度缺失 | 用中位数填充，`satisfaction_is_filled` 标记 | 同上 |
| 实习经历缺失 | 用**众数**填充 | 分类变量 |
| 期望薪资异常值 | IQR（`IQR_FACTOR = 1.5`）剔除，计入 `rows_outlier_removed` | 薪资右偏，均值/标准差法会把正常高薪当异常 |

门禁脚本会校验「ODS 行数 = DWD 行数 + 本次记录的剔除行数」，**不允许静默丢行**。

### 5. 城市与行业里的占位值

数据里「工作城市 = 无」表示没有城市偏好，它不是城市。这类值
（`config.UNSPECIFIED_VALUES`：空串 / 无 / 未填 / 未指定 / 不限 / nan / None / - / /）：

- 统计与推荐排序中**排除**（否则「无」会变成一个城市，还可能是薪资第一）；
- 装载进数据库时归到维表的 **「未指定」成员**上，保证事实表外键非空，
  同时在 BI 里也不会出现一个叫「无」的城市；
- 由门禁 `dwd_unspecified_city_leaked` 兜底检查。

### 6. 权重敏感性

推荐排序用的权重（就业机会 / 满意度 / 薪资）是**人为设定**的，不是从数据里学出来的。
所以除了默认权重，还会用 6 组权重场景各跑一次，报告两件事：

- **秩相关**（Spearman）：新旧排序有多像；
- **TOP1 是否变化**：第一名是不是换人了。

秩相关接近 1 → 排序稳；某场景 TOP1 换人 → 说明「最佳城市」这个说法本身依赖权重假设。
排序表里还会标出样本量不足（`MIN_RECOMMEND_SAMPLES = 5`）的城市。

### 7. 明确不做的事

**不做任何预测**：没有模型、没有外推、没有「明年会怎样」。
所有数字都是对当前这份数据的描述与检验。想加预测，需要另一份数据（时间序列）、
另一套方法（交叉验证），并且要能说清「预测什么、误差多大」——本仓库不做这件事，
报告里也会写明这一句。

---

### 8. 同一列名，两种数据源（问卷 vs 岗位）

`src/sources/boss.py` 把 Boss 直聘岗位数据适配成同一套 7 列契约：**列名不变，语义变了**。

| 列 | 问卷口径 | 岗位口径 |
|---|---|---|
| `期望薪资` | 受访者期望月薪 | 岗位薪资区间中值（元/月） |
| `满意度评分` | 受访者满意度 | 岗位吸引力代理分（0~5，proxy） |
| `专业` | 所学专业 | 岗位方向（标题关键词归类） |
| `毕业去向` | 就业 / 考研 / 出国 / 创业 / 待业 | 岗位类型（实习 / 校招 / 社招） |

规矩：

1. **口径必须随数字一起出现**：报告溯源区的「数据口径」一行由 `config.SOURCE_NOTES` 生成，不能省；
2. **两种口径不可混跑、不可混比**：一份报告只消化一种数据源；「代理分」与「满意度」不可做跨源比较；
3. **文档是单一来源**：[`data_source_boss.md`](data_source_boss.md) 写全映射与解析规则，改规则先改文档；
4. **测试守住声明**：`tests/test_source_boss.py::test_sample_dataset_runs_through_pipeline` 断言溯源里出现
   「数据口径」与「岗位薪资区间中值」，删掉声明测试就红。

---

## 三、改口径的规矩

1. 改阈值 → 只改 `src/config.py`，报告与数据库会自动使用新值并如实打印
   （`ads_run_meta` 记录了本次实际用的阈值）。
2. 改统计方法 → 改 `src/stats.py`，并在本文件第二节补上「为什么这么改」。
3. SQL 里出现的常量（样本量门槛、城市清单）由 `tests/test_sql_layer.py::test_sql_constants_match_config`
   校验，**不许与 `src/config.py` 漂移**。
4. 改完跑 `python tests/test_pipeline.py` 与 `python tests/test_sql_layer.py`；
   报告文字里有 CI 依赖的固定表述（`docs/` 与 `.github/workflows/ci.yml` 各自 grep），别顺手改掉。