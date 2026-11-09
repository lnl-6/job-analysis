-- =============================================================================
-- 04：每个分组相对「全局均值」的偏离 + 组内离散度（方差）
-- =============================================================================
-- 技巧：窗口函数套聚合 —— AVG(AVG(x)) OVER () 先按组求均值，再在全体上求均值，
--       这样不必写第二遍子查询就能拿到全局基准。
-- 说明：方差用 AVG(x*x) - AVG(x)^2 计算（总体方差），
--       样本标准差与变异系数由 Python 统计层给出（避免在 SQL 里开方，跨库最大兼容）。
-- =============================================================================

SELECT dimension,
       group_value,
       sample_size,
       mean_salary,
       overall_mean,
       ROUND(mean_salary - overall_mean, 2)                  AS diff_vs_overall,
       ROUND(mean_salary * 100.0 / overall_mean - 100, 2)    AS pct_vs_overall,
       var_pop
FROM (
  SELECT dimension,
         group_value,
         COUNT(*)                                                     AS sample_size,
         ROUND(AVG(expected_salary), 2)                               AS mean_salary,
         ROUND(AVG(AVG(expected_salary)) OVER (), 2)                  AS overall_mean,
         ROUND(AVG(expected_salary * expected_salary)
               - AVG(expected_salary) * AVG(expected_salary), 2)      AS var_pop
  FROM v_fact_long
  GROUP BY dimension, group_value
) t
ORDER BY dimension, mean_salary DESC;