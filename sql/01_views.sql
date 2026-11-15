-- =============================================================================
-- 01_views.sql —— 分析视图（MySQL 8.0）
-- =============================================================================
-- 视图分两类：
--   1) 事实侧视图（v_fact_long / v_salary_percentiles_nearest_rank / v_group_share）
--      —— 用窗口函数直接从明细算统计，是「SQL 侧的独立实现」，
--         用于与 Python 统计层（ads_group_stats）交叉核对口径差异。
--   2) 结果侧视图（v_comparison_dashboard / v_quality_* / v_city_major_gap）
--      —— 只做格式化与标签，数字来自 ads_* 结果表，供 Power BI 直接使用。
--
-- 口径说明（重要）：SQL 侧分位数用 nearest-rank 定义（最小的秩 ≥ n·p 的那个值），
--   Python 侧用线性插值（numpy 默认）。两者会有小数点级别的差异，
--   报告与 BI 的对外数字一律以 Python 侧为准，SQL 侧用于交叉核对。
-- =============================================================================

SET NAMES utf8mb4;
USE employment_analysis;

-- -----------------------------------------------------------------------------
-- 事实侧：把 5 个维度摊平成一张长表（维度, 取值, 明细度量）
--   这样后续所有窗口函数只需 PARTITION BY (dimension, group_value)
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_fact_long;
CREATE VIEW v_fact_long AS
SELECT 'education' AS dimension, e.education AS group_value,
       f.expected_salary, f.satisfaction, f.has_internship
FROM dwd_survey_clean f
JOIN dim_education e ON e.education_key = f.education_key
UNION ALL
SELECT 'major', m.major, f.expected_salary, f.satisfaction, f.has_internship
FROM dwd_survey_clean f
JOIN dim_major m ON m.major_key = f.major_key
UNION ALL
SELECT 'city', c.city, f.expected_salary, f.satisfaction, f.has_internship
FROM dwd_survey_clean f
JOIN dim_city c ON c.city_key = f.city_key
UNION ALL
SELECT 'direction', d.direction, f.expected_salary, f.satisfaction, f.has_internship
FROM dwd_survey_clean f
JOIN dim_direction d ON d.direction_key = f.direction_key
UNION ALL
SELECT 'industry', i.industry, f.expected_salary, f.satisfaction, f.has_internship
FROM dwd_survey_clean f
JOIN dim_industry i ON i.industry_key = f.industry_key;

-- -----------------------------------------------------------------------------
-- 事实侧：分组分位数（nearest-rank，纯整数运算，不依赖任何方言特有函数）
--   P25 = 秩满足 rn*4 >= n 的最小薪资；P50 = rn*2 >= n；P75 = rn*4 >= 3n
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_salary_percentiles_nearest_rank;
CREATE VIEW v_salary_percentiles_nearest_rank AS
WITH ranked AS (
  SELECT dimension, group_value, expected_salary,
         ROW_NUMBER() OVER (PARTITION BY dimension, group_value ORDER BY expected_salary) AS rn,
         COUNT(*)     OVER (PARTITION BY dimension, group_value)                          AS n
  FROM v_fact_long
  WHERE expected_salary IS NOT NULL
)
SELECT dimension,
       group_value,
       n AS sample_size,
       MIN(CASE WHEN rn * 4 >= n     THEN expected_salary END) AS p25_nearest_rank,
       MIN(CASE WHEN rn * 2 >= n     THEN expected_salary END) AS p50_nearest_rank,
       MIN(CASE WHEN rn * 4 >= n * 3 THEN expected_salary END) AS p75_nearest_rank,
       MIN(expected_salary) AS min_salary,
       MAX(expected_salary) AS max_salary
FROM ranked
GROUP BY dimension, group_value, n;

-- -----------------------------------------------------------------------------
-- 事实侧：组内占比 + 与所属维度均值的偏离（窗口函数套聚合）
--   SUM(COUNT(*)) OVER (PARTITION BY dimension) —— 先按组聚合，再在维度内求总和
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_group_share;
CREATE VIEW v_group_share AS
SELECT dimension,
       group_value,
       COUNT(*)                                                                        AS sample_size,
       ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY dimension), 2)        AS share_pct,
       ROUND(AVG(expected_salary), 2)                                                  AS mean_salary,
       ROUND(AVG(AVG(expected_salary)) OVER (PARTITION BY dimension), 2)               AS dimension_mean_salary,
       ROUND(AVG(expected_salary) - AVG(AVG(expected_salary)) OVER (PARTITION BY dimension), 2) AS diff_vs_dimension_mean,
       ROUND(AVG(satisfaction), 2)                                                     AS mean_satisfaction,
       ROUND(AVG(CASE WHEN has_internship = 1 THEN 1.0 ELSE 0.0 END) * 100, 2)         AS internship_rate_pct
FROM v_fact_long
GROUP BY dimension, group_value;

-- -----------------------------------------------------------------------------
-- 结果侧：最近一次运行（门禁与 BI 都以「最新一次装载」为准）
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_latest_run;
CREATE VIEW v_latest_run AS
SELECT run_id,
       load_batch,
       data_file,
       rows_raw,
       rows_clean,
       rows_invalid_salary,
       rows_outlier_removed,
       run_at
FROM ads_run_meta
ORDER BY run_at DESC, run_id DESC
LIMIT 1;

-- -----------------------------------------------------------------------------
-- 结果侧：假设检验结果 + 人话标签（Power BI 直接绑定这张视图）
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_comparison_dashboard;
CREATE VIEW v_comparison_dashboard AS
SELECT run_id,
       comparison_key,
       label,
       group_a,
       group_b,
       n_a,
       n_b,
       mean_a,
       mean_b,
       mean_diff,
       ci_low,
       ci_high,
       p_welch,
       p_holm,
       p_mannwhitney,
       cohens_d,
       effect_label,
       verdict,
       CASE WHEN sufficient = 0 THEN '样本不足，不给结论'
            WHEN significant = 1 THEN '差异显著（Holm 校正后）'
            ELSE '差异不显著' END AS significance_label,
       CASE WHEN cohens_d IS NULL THEN '—'
            WHEN cohens_d < 1 AND cohens_d > -1 THEN '差异很小，别当卖点'
            ELSE '差异较大，值得往下看' END AS practical_note
FROM ads_comparisons;

-- -----------------------------------------------------------------------------
-- 结果侧：体检汇总与明细
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_quality_summary;
CREATE VIEW v_quality_summary AS
SELECT run_id,
       COUNT(*)      AS checks_total,
       SUM(flagged)  AS checks_flagged
FROM ads_quality_checks
GROUP BY run_id;

DROP VIEW IF EXISTS v_quality_flagged;
CREATE VIEW v_quality_flagged AS
SELECT run_id, seq, item, detail, verdict
FROM ads_quality_checks
WHERE flagged = 1;

-- -----------------------------------------------------------------------------
-- 结果侧：专业 × 城市 矩阵里，每个专业内部谁高谁低（窗口函数求组内均值）
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_city_major_gap;
CREATE VIEW v_city_major_gap AS
SELECT run_id,
       major,
       city,
       sample_size,
       mean_salary,
       ROUND(AVG(mean_salary) OVER (PARTITION BY major), 2)                          AS major_mean_salary,
       ROUND(mean_salary - AVG(mean_salary) OVER (PARTITION BY major), 2)            AS diff_vs_major_mean,
       RANK() OVER (PARTITION BY major ORDER BY mean_salary DESC)                    AS rank_in_major
FROM ads_major_city_matrix
WHERE mean_salary IS NOT NULL;