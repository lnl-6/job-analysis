-- =============================================================================
-- quality_checks.sql —— 阻断式数据质量门禁（MySQL 8.0）
-- =============================================================================
-- 用法：mysql ... < sql/quality_checks.sql   或   python scripts/load_to_mysql.py
-- 约定：返回单结果集，每行一个检查项：
--         severity = '阻断'  → violations > 0 即视为本次装载不合格（运行器返回非零退出码）
--         severity = '提示'  → 只记录不阻断（与报告里的「数据质量体检」呼应）
-- 口径：全部检查都针对**最新一次装载**（v_latest_run）——
--   ODS/DWD 按 load_batch 过滤，ADS 按 run_id 过滤，
--   否则库里多跑几次之后，门禁会被历史数据误伤。
-- 为什么放在 SQL 里：装载是 SQL 层的事，门禁就该贴着装载执行；
--   Python 侧的 src/quality.py 负责「解释力 / 分布形态」这类统计体检，两者互补不重复。
-- =============================================================================

SET NAMES utf8mb4;
USE employment_analysis;

SELECT 'ods_rows_vs_dwd_rows' AS check_name,
       '阻断' AS severity,
       ABS((SELECT COUNT(*) FROM ods_employment_survey
             WHERE load_batch = (SELECT load_batch FROM v_latest_run))
           - (SELECT COUNT(*) FROM dwd_survey_clean
             WHERE load_batch = (SELECT load_batch FROM v_latest_run))
           - (SELECT rows_invalid_salary + rows_outlier_removed FROM v_latest_run)) AS violations,
       '最新一次装载：ODS 行数 = DWD 行数 + 本次记录的剔除行数（无效薪资 / IQR 异常值）' AS detail

UNION ALL
SELECT 'dwd_non_positive_salary', '阻断',
       (SELECT COUNT(*) FROM dwd_survey_clean
         WHERE expected_salary <= 0
           AND load_batch = (SELECT load_batch FROM v_latest_run)),
       '期望薪资必须 > 0（清洗阶段就该剔除；schema 的 CHECK 约束是第二道防线）'

UNION ALL
SELECT 'dwd_satisfaction_out_of_range', '阻断',
       (SELECT COUNT(*) FROM dwd_survey_clean
         WHERE (satisfaction < 0 OR satisfaction > 5)
           AND load_batch = (SELECT load_batch FROM v_latest_run)),
       '满意度评分必须在 0~5 之间'

UNION ALL
SELECT 'dwd_unspecified_city_leaked', '阻断',
       (SELECT COUNT(*) FROM dwd_survey_clean f
          JOIN dim_city c ON c.city_key = f.city_key
         WHERE c.city IN ('', '无', '未填', '不限', '-', '/')
           AND f.load_batch = (SELECT load_batch FROM v_latest_run)),
       '「无 / 未填」不是城市：应归到维表的「未指定」成员，不允许原样进维表'

UNION ALL
SELECT 'dwd_missing_dimension_key', '阻断',
       (SELECT COUNT(*) FROM dwd_survey_clean f
         WHERE (f.major_key IS NULL OR f.education_key IS NULL
                OR f.direction_key IS NULL OR f.city_key IS NULL)
           AND f.load_batch = (SELECT load_batch FROM v_latest_run)),
       '事实表的维度键不允许为空'

UNION ALL
SELECT 'ads_run_meta_exists', '阻断',
       CASE WHEN (SELECT COUNT(*) FROM v_latest_run) = 1 THEN 0 ELSE 1 END,
       '必须存在溯源记录（v_latest_run 取最近一次运行）'

UNION ALL
SELECT 'ads_group_stats_not_empty', '阻断',
       CASE WHEN (SELECT COUNT(*) FROM ads_group_stats
                   WHERE run_id = (SELECT run_id FROM v_latest_run)) > 0 THEN 0 ELSE 1 END,
       '最新一次运行的分组统计结果表不允许为空'

UNION ALL
SELECT 'ads_comparisons_missing_verdict', '阻断',
       (SELECT COUNT(*) FROM ads_comparisons
         WHERE (verdict IS NULL OR verdict = '')
           AND run_id = (SELECT run_id FROM v_latest_run)),
       '每条假设检验都必须带判定文案（样本不足也要写明样本不足）'

UNION ALL
SELECT 'ads_matrix_small_sample_leaked', '阻断',
       (SELECT COUNT(*) FROM ads_major_city_matrix
         WHERE insufficient = 1 AND mean_salary IS NOT NULL
           AND run_id = (SELECT run_id FROM v_latest_run)),
       '样本量不足的交叉格子必须留空（不允许展示小样本均值）'

UNION ALL
SELECT 'ads_group_sample_ok_consistency', '阻断',
       (SELECT COUNT(*) FROM ads_group_stats
         WHERE ((sample_ok = 1 AND sample_size < 30) OR (sample_ok = 0 AND sample_size >= 30))
           AND run_id = (SELECT run_id FROM v_latest_run)),
       'sample_ok 标记必须与样本量门槛（30）一致'

UNION ALL
SELECT 'ads_significant_requires_sufficient', '阻断',
       (SELECT COUNT(*) FROM ads_comparisons
         WHERE significant = 1 AND sufficient = 0
           AND run_id = (SELECT run_id FROM v_latest_run)),
       '样本不足的对比不允许被判为显著'

UNION ALL
SELECT 'ods_missing_gender_over_10pct', '提示',
       CASE WHEN (SELECT COUNT(*) FROM ods_employment_survey
                   WHERE (gender IS NULL OR gender = '')
                     AND load_batch = (SELECT load_batch FROM v_latest_run)) * 100.0
                 / NULLIF((SELECT COUNT(*) FROM ods_employment_survey
                            WHERE load_batch = (SELECT load_batch FROM v_latest_run)), 0) > 10
            THEN 1 ELSE 0 END,
       '最新一次装载里性别缺失率超过 10% 时提示（缺失过多会让该维度不可用）'

UNION ALL
SELECT 'ods_duplicate_respondent_id', '提示',
       (SELECT COUNT(*) - COUNT(DISTINCT respondent_id) FROM ods_employment_survey
         WHERE load_batch = (SELECT load_batch FROM v_latest_run)),
       '最新一次装载里问卷编号重复数量（真实问卷数据常见，仅提示）';