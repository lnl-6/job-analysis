# 数据字典

**权威来源是 schema 本身**：`sql/00_schema.sql`（MySQL 8）里每个字段都带中文 `COMMENT`，
`sql/schema_sqlite.sql` 是列名完全一致的本地镜像。本文件是它的整理版，
方便在浏览器里直接读——改 schema 时请同步改这里（`docs/metrics.md` 第三节有改动规矩）。

## 分层

| 层 | 表 | 粒度（一行代表什么） | 写入者 | 用途 |
| --- | --- | --- | --- | --- |
| ODS | `ods_employment_survey` | 一份问卷 | `src/db.py::_load_ods` | 原始落地：**全部按字符串存**，不做类型推断、不改一个字符 |
| DWD | `dwd_survey_clean` + 5 张维表 | 一份**清洗后**的问卷 | `_load_dwd` / `_load_dimensions` | 分析用的明细事实表，带外键与约束 |
| ADS | `ads_*`（7 张） | 一次运行的**结果** | `_load_run_meta` 等 | 给 SQL 查询、看板与 BI 直接消费的成品数据 |

关键约定：

- **ODS 不改数据**：所有清洗（缺失填充、无效薪资剔除、IQR 剔除）都发生在 Python 侧，
  ODS 保留原始字符串，方便随时回头核对「这条记录原样是什么」。
- **DWD 不允许静默丢行**：`ODS 行数 = DWD 行数 + ads_run_meta 记录的剔除行数`，
  由门禁 `ods_rows_vs_dwd_rows` 强制校验。
- **ADS 只搬运、不重算**：数值来自 `src/analysis.py` / `src/stats.py` / `src/quality.py`，
  数据库与 BI 不再算第二遍（见 `docs/metrics.md` 第一条原则）。

---

## 一、ODS 层

### `ods_employment_survey` — 原始问卷（主键：`load_batch` + `source_row_no`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `load_batch` | VARCHAR(32) | 装载批次 = 数据文件 SHA-256 前 12 位（同一份文件重复装载是同一批，先删后插） |
| `source_row_no` | INT | 数据行号，CSV 第 2 行记为 1（与 Python 侧 DataFrame 的 index 对齐） |
| `respondent_id` | VARCHAR(32) | 问卷编号（原始 `ID` 列） |
| `gender` | VARCHAR(16) | 性别（原始字符串；本项目**不做性别维度分析**，仅入库备查） |
| `major` | VARCHAR(64) | 专业 |
| `education` | VARCHAR(16) | 学历 |
| `direction` | VARCHAR(32) | 毕业去向 |
| `industry` | VARCHAR(64) | 就业行业 |
| `expected_salary` | VARCHAR(32) | 期望薪资（原样字符串，可能有空值或 `-2100` 这类无效值——清洗在 Python 侧做） |
| `city` | VARCHAR(32) | 工作城市（可能为「无 / 未填」） |
| `satisfaction` | VARCHAR(32) | 满意度评分（原始字符串） |
| `has_internship` | VARCHAR(16) | 是否有实习经历 |
| `loaded_at` | DATETIME | 入库时间（默认当前时间） |

---

## 二、DWD 层

### 维表

| 表 | 字段 | 说明 |
| --- | --- | --- |
| `dim_major` | `major_key`（PK, 自增）、`major`（UNIQUE） | 专业 |
| `dim_city` | `city_key`（PK, 自增）、`city`（UNIQUE） | 城市；「无 / 未填」等占位值统一归到成员 **「未指定」** |
| `dim_education` | `education_key`（PK, 自增）、`education`（UNIQUE）、`education_rank` | 学历 + 序号（1=大专 2=本科 3=硕士 4=博士，其他=99，供 `ORDER BY` 用） |
| `dim_direction` | `direction_key`（PK, 自增）、`direction`（UNIQUE） | 毕业去向 |
| `dim_industry` | `industry_key`（PK, 自增）、`industry`（UNIQUE） | 就业行业（该维度允许为空，占位值不建成员） |

### `dwd_survey_clean` — 清洗后明细（主键：`respondent_uid`；唯一键：`load_batch` + `source_row_no`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `respondent_uid` | BIGINT | 代理键（自增） |
| `load_batch` | VARCHAR(32) | 装载批次（数据指纹前 12 位） |
| `source_row_no` | INT | 来源行号（与 ODS 对齐，幂等键的一半） |
| `respondent_id` | VARCHAR(32) | 问卷编号 |
| `gender` | VARCHAR(8) | 性别（缺失已按众数填充） |
| `major_key` / `education_key` / `direction_key` / `city_key` | INT | 外键（`city_key` 等**非空**） |
| `industry_key` | INT | 外键（可空） |
| `expected_salary` | DECIMAL(10,2) | 期望薪资（元）：≥ 0；缺失按中位数填充 |
| `satisfaction` | DECIMAL(3,1) | 满意度评分（0~5）；缺失按中位数填充 |
| `has_internship` | TINYINT(1) | 是否有实习经历：1=是 0=否 |
| `salary_is_filled` | TINYINT(1) | 薪资是否为填充值：1=填充 0=原始（**结论里可以据此剔除填充样本重算**） |
| `satisfaction_is_filled` | TINYINT(1) | 满意度是否为填充值 |
| `loaded_at` | DATETIME | 入库时间 |

约束：5 个外键（`fk_dwd_*`）、`ck_dwd_salary`（`expected_salary >= 0`）、
`ck_dwd_satisfaction`（`0 <= satisfaction <= 5`）。约束是第一道防线，门禁是第二道。

---

## 三、ADS 层（结果表）

### `ads_run_meta` — 溯源（主键：`run_id`）

`run_id`（数据 SHA-256 前 12 位 + 时间戳）、`load_batch`、`data_file`、`data_sha256`（全长）、
`rows_raw`、`rows_clean`、`rows_invalid_salary`、`rows_outlier_removed`、`missing_filled_json`、
`min_group_size`、`min_recommend_samples`、`ci_level`、`alpha`、
`python_version`、`pandas_version`、`numpy_version`、`scipy_version`、`run_at`。

**这张表回答「这个数字是谁、用哪份数据、什么阈值、什么环境算出来的」** ——
报告里的溯源区打印的就是同一批信息。

### `ads_group_stats` — 分组统计长表（主键：`run_id` + `dimension` + `group_value`）

| 字段 | 说明 |
| --- | --- |
| `dimension` | 维度：`city` / `major` / `education` / `direction` / `industry`（一张长表装 5 个维度，BI 切片方便） |
| `group_value` | 该维度的取值（已排除「无 / 未指定」这类占位值） |
| `sample_size` | 样本量 |
| `mean_salary` / `median_salary` / `p25` / `p50` / `p75` / `salary_std` | 薪资统计量（分位数为线性插值口径） |
| `salary_ci_low` / `salary_ci_high` | 均值的 95% 置信区间 |
| `mean_satisfaction` / `internship_rate` | 满意度均值、实习占比（%） |
| `sample_ok` | 是否达到样本量门槛（1=达标；不达标时不给「差异/显著」结论） |

### `ads_major_city_matrix` — 专业 × 城市（主键：`run_id` + `major` + `city`）

`sample_size`、`mean_salary`（**样本量不足时为 NULL**）、`insufficient`（是否被置空）。
门禁 `ads_matrix_small_sample_leaked` 保证「小样本格子永远是空的」。

### `ads_comparisons` — 假设检验（主键：`run_id` + `comparison_key`）

`comparison_key`（如 `edu_phd_vs_bachelor`）、`label`、`group_a`/`group_b`、`n_a`/`n_b`、
`mean_a`/`mean_b`、`mean_diff`（A−B）、`ci_low`/`ci_high`、
`p_welch`、`p_holm`（判定用它）、`p_mannwhitney`、`cohens_d`、`effect_label`、
`sufficient`、`significant`、`verdict`（样本不足也会写明）。

### `ads_quality_checks` — 数据质量体检（主键：`run_id` + `seq`）

`item`（体检项）、`detail`（含具体数值的描述）、`verdict`（以「提示」开头的会被报告点名）、
`flagged`。来源是 `src/quality.py::audit`：缺失率、重复行、分组解释力（ANOVA + eta²）、
分布形态、取值集中度。

### `ads_weight_sensitivity` — 权重敏感性（主键：`run_id` + `seq`）

`scenario`（6 组权重场景）、`top1_city`、`spearman_vs_default`（与默认排序的秩相关）、
`top1_same`（0/1/NULL——NULL 表示样本不足无法评估）、`note`。

---

## 四、视图（`sql/01_views.sql`）

| 视图 | 说明 |
| --- | --- |
| `v_fact_long` | 5 个维度 UNION ALL 摊平成长表（`dimension` + `group_value` + 明细度量），后面所有窗口函数只按它分组 |
| `v_salary_percentiles_nearest_rank` | 分组分位数（nearest-rank 定义，纯整数判据），与 Python 线性插值口径交叉核对 |
| `v_group_share` | 组内占比 + 组内均值与所属维度均值的偏离（`AVG(AVG(x)) OVER (...)`） |
| `v_latest_run` | 最近一次运行（门禁与 BI 都以「最新一次装载」为准） |
| `v_comparison_dashboard` | 检验结果 + `significance_label` / `practical_note`（给 BI 直接用的人话标签） |
| `v_quality_summary` / `v_quality_flagged` | 体检汇总与需要提示的明细 |
| `v_city_major_gap` | 专业 × 城市矩阵中每组与专业内均值的偏离、组内排名 |

## 五、门禁（`sql/quality_checks.sql`）

13 项检查，返回单结果集（`check_name` / `severity` / `violations` / `detail`）。
`severity = '阻断'` 的项只要有 1 条违规，装载脚本就以退出码 1 结束。
检查全部针对**最新一次装载**（`v_latest_run`），避免历史数据误伤。