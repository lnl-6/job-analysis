# Power BI 呈现层：模型、度量值与报表清单

这一层**只负责呈现**：数字来自分析层算好的结果表，Power BI 里不再重算对外指标。
（唯一例外见第 4 节末尾的口径说明，那里也需要交叉核对。）

---

## 1. 准备数据源

```bash
# 起 MySQL 8（首次启动会自动执行 sql/00_schema.sql 与 sql/01_views.sql）
cp .env.example .env
docker compose up -d mysql

# 把一次完整分析装载进去（含阻断式门禁）
python scripts/load_to_mysql.py --backend mysql --host 127.0.0.1 \
    --user root --password employment --database employment_analysis
```

> 没有 Docker？装 MySQL 8 后手动执行 `mysql -u root -p < sql/00_schema.sql`
> 与 `mysql -u root -p < sql/01_views.sql`，再跑同一条装载命令。
> 若本机两者都没有：Power BI 读不了本仓库的 SQLite 镜像（ODBC SQLite 驱动太折腾），
> 直接看 `output/report.md` 与图表即可，BI 这一步可以留给面试演示环境。

## 2. 取哪几张表

**推荐只连两类表**，其余（ODS）不要连进模型：

| 用途 | 表 / 视图 | 说明 |
| --- | --- | --- |
| 星型模型（明细钻取） | `dwd_survey_clean` + 5 张 `dim_*` | 事实 + 维度，用于「按学历/城市/专业切片看薪资分布」 |
| 结果呈现（成品数字） | `ads_group_stats`、`ads_comparisons`、`ads_major_city_matrix`、`ads_weight_sensitivity`、`ads_quality_checks`、`ads_run_meta` | 与报告数字**同源**，直接拖字段即可 |
| 人话标签 | `v_comparison_dashboard`、`v_city_major_gap`、`v_quality_flagged`、`v_latest_run` | 视图里已经带好 `significance_label`、`practical_note`、组内排名等 |

小技巧：`ads_group_stats` 是**一张长表装 5 个维度**（`dimension` + `group_value`），
配一个切片器就能在「学历 / 专业 / 城市 / 去向 / 行业」之间切换，不用建 5 张表。

## 3. 关系与建模

```
dim_education 1 ── N dwd_survey_clean
dim_major     1 ── N dwd_survey_clean
dim_city      1 ── N dwd_survey_clean
dim_direction 1 ── N dwd_survey_clean
dim_industry  1 ── N dwd_survey_clean      （industry_key 可空，单向过滤）
ads_run_meta  1 ── N ads_group_stats / ads_comparisons / ads_major_city_matrix / ads_weight_sensitivity / ads_quality_checks
```

- 关系方向：**单向**（维度 → 事实），交叉筛选方向保持默认「单一」。
- 所有 `*_key`、`load_batch`、`run_id`、`seq` 列建议**隐藏**（模型视图里右键隐藏），
  报表里不暴露代理键。
- 只保留最新一次运行的数据：在 `ads_run_meta` 上用 `v_latest_run` 过滤，
  或直接对结果表加相对日期筛选；否则多次装载会让同一指标出现多行。
- 把维表里的 `education_rank` 设为「按列排序」的排序依据，学历轴就会按
  大专 → 本科 → 硕士 → 博士 排，而不是按拼音。

## 4. 度量值清单（DAX，可直接粘贴）

整体指标（来源：事实表 —— 与 Python 侧同一个算术平均定义）：

```dax
样本量 = COUNTROWS ( dwd_survey_clean )

平均期望薪资 = AVERAGE ( dwd_survey_clean[expected_salary] )

薪资中位数 = MEDIAN ( dwd_survey_clean[expected_salary] )

P25 = PERCENTILE.INC ( dwd_survey_clean[expected_salary], 0.25 )
P50 = PERCENTILE.INC ( dwd_survey_clean[expected_salary], 0.5 )
P75 = PERCENTILE.INC ( dwd_survey_clean[expected_salary], 0.75 )

平均满意度 = AVERAGE ( dwd_survey_clean[satisfaction] )

实习率 =
DIVIDE (
    CALCULATE ( COUNTROWS ( dwd_survey_clean ), dwd_survey_clean[has_internship] = 1 ),
    COUNTROWS ( dwd_survey_clean )
)

填充样本占比 =
DIVIDE (
    CALCULATE ( COUNTROWS ( dwd_survey_clean ), dwd_survey_clean[salary_is_filled] = 1 ),
    COUNTROWS ( dwd_survey_clean )
)
```

结果表指标（来源：`ads_*` —— 与报告数字逐字同源）：

```dax
分组均值（安全版） =
VAR ok = SELECTEDVALUE ( ads_group_stats[sample_ok] )
RETURN IF ( ok = 0, BLANK (), SELECTEDVALUE ( ads_group_stats[mean_salary] ) )
-- 样本量不足的分组直接留空，和热力图、报告的处理保持一致

分组样本量 = SELECTEDVALUE ( ads_group_stats[sample_size] )

显著对比数 = CALCULATE ( COUNTROWS ( ads_comparisons ), ads_comparisons[significant] = 1 )

不显著对比数 = CALCULATE ( COUNTROWS ( ads_comparisons ), ads_comparisons[sufficient] = 1, ads_comparisons[significant] = 0 )

小样本分组占比 =
DIVIDE (
    CALCULATE ( COUNTROWS ( ads_group_stats ), ads_group_stats[sample_ok] = 0 ),
    COUNTROWS ( ads_group_stats )
)

判定（人话） = SELECTEDVALUE ( ads_comparisons[verdict] )

均值差 = SELECTEDVALUE ( ads_comparisons[mean_diff] )

权重稳健性（最小秩相关） = MIN ( ads_weight_sensitivity[spearman_vs_default] )

体检提示数 = CALCULATE ( COUNTROWS ( ads_quality_checks ), ads_quality_checks[flagged] = 1 )
```

**口径提醒**：`PERCENTILE.INC` 与 Python 侧的线性插值口径一致；
而 SQL 视图 `v_salary_percentiles_nearest_rank` 用的是 nearest-rank 定义，
三者会有小数点级别的差异。**以 Python / 报告为准**，差异本身就是
`sql/queries/02_salary_percentiles_cross_check.sql` 要量化的东西。

**门禁不落库**：`sql/quality_checks.sql` 是装载流水线的退出码（0 = 通过），
不属于数据集内容；BI 里能看到的 `ads_quality_checks` 是 Python 侧的「数据质量体检」，
两者互补、不要混为一谈。

## 5. 报表页面（3 页 + 1 页可选）

**第 1 页 · 总览**
- 卡片：样本量、平均期望薪资、薪资中位数、平均满意度、实习率、填充样本占比
- 柱状图：学历 → 平均期望薪资（轴按 `education_rank` 排序）
- 条形图：城市 → 平均期望薪资 + 样本量（同时显示，避免只看均值）
- 卡片：本次运行信息（`ads_run_meta`：数据指纹、行数、阈值、环境版本、运行时间）——
  这一块让看板「可追溯」，评审最爱问的就是这个

**第 2 页 · 对比与显著性**
- 表格：`v_comparison_dashboard` 全列（对比 / 两组样本量 / 均值 / 均值差 / 95% 置信区间 /
  p（Welch）/ p（Holm）/ Cohen's d / 判定 / 显著性标签）
- 散点图：横轴 `cohens_d`、纵轴 `p_holm` —— 一眼看出「显著但效应很小」的那些对比
- 卡片：显著对比数、不显著对比数、小样本分组占比

**第 3 页 · 城市与专业矩阵**
- 矩阵：行 = 专业、列 = 城市、值 = 平均期望薪资（来源 `ads_major_city_matrix`）
  + 条件格式色阶；样本量不足的格子天然为空（不展示小样本均值）
- 表格：`v_city_major_gap`（每个专业内部的排名与相对专业均值的偏离）
- 表格：`ads_weight_sensitivity`（6 组权重场景：TOP1 城市、与默认排序的秩相关、TOP1 是否变化）
  + 卡片「权重稳健性（最小秩相关）」

**第 4 页 · 数据质量（可选，但很加分）**
- 表格：`v_quality_flagged`（需要提示的体检项）
- 卡片：体检提示数、缺失率最高项（`ads_quality_checks` 里 item 含「缺失率」的行）

## 6. 刷新流程

```
数据更新 → python main.py（报告 + 图表）
        → python scripts/load_to_mysql.py --backend mysql ...（装载 + 门禁）
        → Power BI「刷新」
```

**门禁返回非零就不要刷新看板**：数据没通过校验时，看板上任何数字都不该更新。
（CI 里的 `mysql` job 每次提交都会走一遍同样的流程，等于持续验证这条链路。）

## 7. 验收清单

- [ ] `docker compose up -d mysql` 起来且 healthcheck 通过
- [ ] 装载脚本退出码 0，13 项门禁无阻断级违规
- [ ] Power BI 连上 MySQL，只连第 2 节列出的表，ODS 不连
- [ ] 关系按第 3 节建好，键列已隐藏，`education_rank` 已设为排序依据
- [ ] 第 4 节的度量值全部粘贴成功且数字与 `output/report.md` 对得上
- [ ] 三页报表完成，另存 `.pbix`
- [ ] 截图 2~3 张放进 `docs/images/`（可选：写进 README 的「呈现层」小节）