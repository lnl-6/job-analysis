-- =============================================================================
-- 05：专业 × 主要城市 的样本量矩阵（行转列的经典写法）
-- =============================================================================
-- 技巧：条件聚合（SUM(CASE WHEN ... THEN 1 ELSE 0 END)）把长表转成宽表。
-- 注意：城市清单与 src/config.py 的 MAIN_CITIES 一致
--       （tests/test_sql_layer.py 会校验两边不许漂移）。
-- =============================================================================

SELECT m.major,
       COUNT(*)                                                        AS total_samples,
       SUM(CASE WHEN c.city = '上海' THEN 1 ELSE 0 END)                 AS "上海",
       SUM(CASE WHEN c.city = '北京' THEN 1 ELSE 0 END)                 AS "北京",
       SUM(CASE WHEN c.city = '广州' THEN 1 ELSE 0 END)                 AS "广州",
       SUM(CASE WHEN c.city = '深圳' THEN 1 ELSE 0 END)                 AS "深圳",
       SUM(CASE WHEN c.city = '武汉' THEN 1 ELSE 0 END)                 AS "武汉",
       SUM(CASE WHEN c.city = '南京' THEN 1 ELSE 0 END)                 AS "南京",
       ROUND(AVG(f.expected_salary), 2)                                AS mean_salary
FROM dwd_survey_clean f
JOIN dim_major m ON m.major_key = f.major_key
JOIN dim_city  c ON c.city_key  = f.city_key
GROUP BY m.major
ORDER BY total_samples DESC, m.major;