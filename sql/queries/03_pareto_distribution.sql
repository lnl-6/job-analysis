-- =============================================================================
-- 03：薪资的帕累托结构 —— 最高的 20% 样本占了多少薪资总量
-- =============================================================================
-- 技巧：SUM(...) OVER (ORDER BY ...) 累计求和 + 带排名的占比分带
-- 用途：判断「均值」是否被少数高薪样本拉动（右偏分布的量化证据）
-- =============================================================================

WITH ordered AS (
  SELECT f.expected_salary,
         ROW_NUMBER() OVER (ORDER BY f.expected_salary DESC) AS rn,
         COUNT(*)     OVER ()                                AS n,
         SUM(f.expected_salary) OVER ()                      AS total_salary
  FROM dwd_survey_clean f
),
banded AS (
  SELECT expected_salary,
         n,
         total_salary,
         CASE WHEN rn * 5 <= n THEN '前 20% 高薪'
              ELSE '其余 80%' END AS salary_band
  FROM ordered
)
SELECT salary_band,
       COUNT(*)                                              AS sample_size,
       ROUND(AVG(expected_salary), 2)                        AS band_mean_salary,
       ROUND(SUM(expected_salary), 2)                        AS band_salary_sum,
       ROUND(SUM(expected_salary) * 100.0 / MAX(total_salary), 2) AS band_share_pct
FROM banded
GROUP BY salary_band
ORDER BY band_mean_salary DESC;