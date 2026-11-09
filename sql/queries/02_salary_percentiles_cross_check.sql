-- =============================================================================
-- 02：同一指标的两套独立实现并排核对（SQL nearest-rank vs Python 线性插值）
-- =============================================================================
-- 为什么这么做：分位数有多个定义，SQL 侧用 nearest-rank（只依赖整数运算，
--   任何数据库都能跑），Python 侧用 numpy 的线性插值。把两者并排放，
--   差异就是口径差异本身 —— 而不是「两边数字对不上」这种说不清的问题。
-- 对外数字（报告 / BI）一律以 Python 侧为准。
-- =============================================================================

SELECT s.dimension,
       s.group_value,
       s.sample_size,
       s.p25_nearest_rank  AS sql_p25,
       g.p25               AS python_p25,
       ROUND(s.p25_nearest_rank - g.p25, 2) AS diff_p25,
       s.p50_nearest_rank  AS sql_p50,
       g.p50               AS python_p50,
       ROUND(s.p50_nearest_rank - g.p50, 2) AS diff_p50,
       s.p75_nearest_rank  AS sql_p75,
       g.p75               AS python_p75,
       ROUND(s.p75_nearest_rank - g.p75, 2) AS diff_p75
FROM v_salary_percentiles_nearest_rank s
JOIN ads_group_stats g
  ON g.dimension = s.dimension
 AND g.group_value = s.group_value
ORDER BY ABS(s.p50_nearest_rank - g.p50) DESC, s.dimension, s.group_value;