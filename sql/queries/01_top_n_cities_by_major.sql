-- =============================================================================
-- 01：每个专业下，样本量达标的城市里薪资前 3 名
-- =============================================================================
-- 需求：分组内 TOP-N（每个专业取 3 个城市）
-- 技巧：CTE 先聚合 + 窗口函数 ROW_NUMBER() 分组排序（MySQL 8 没有 QUALIFY，必须套一层 CTE）
-- 注意：30 与 src/config.py 的 MIN_GROUP_SIZE 一致（tests/test_sql_layer.py 会校验两边不许漂移）
-- =============================================================================

WITH city_stats AS (
  SELECT m.major,
         c.city,
         COUNT(*)                          AS sample_size,
         ROUND(AVG(f.expected_salary), 2)  AS mean_salary
  FROM dwd_survey_clean f
  JOIN dim_major m ON m.major_key = f.major_key
  JOIN dim_city  c ON c.city_key  = f.city_key
  GROUP BY m.major, c.city
),
ranked AS (
  SELECT cs.major,
         cs.city,
         cs.sample_size,
         cs.mean_salary,
         ROW_NUMBER() OVER (PARTITION BY cs.major ORDER BY cs.mean_salary DESC, cs.city) AS rn
  FROM city_stats cs
  WHERE cs.sample_size >= 30
)
SELECT major,
       city,
       sample_size,
       mean_salary,
       rn AS rank_in_major
FROM ranked
WHERE rn <= 3
ORDER BY major, rn;