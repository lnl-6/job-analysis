-- =============================================================================
-- schema_job_sqlite.sql —— 岗位市场层的 SQLite 镜像（与 02_job_market.sql 对齐）
-- =============================================================================
-- 表名 / 列名 / 约束语义与 MySQL 版完全一致，差异只在方言：
--   * 无 ENGINE / COMMENT / DATABASE / USE 语句
--   * LONGTEXT / DECIMAL / DATE → TEXT / REAL（SQLite 类型亲和，不影响口径）
--   * 视图用 DROP VIEW IF EXISTS + CREATE VIEW（SQLite 没有 CREATE OR REPLACE VIEW）
-- 执行顺序：schema_sqlite.sql → schema_job_sqlite.sql（依赖 dim_city / dim_industry）。
-- =============================================================================

PRAGMA foreign_keys = ON;

-- 视图先删（避免依赖颠倒）
DROP VIEW IF EXISTS v_job_market_overview;
DROP VIEW IF EXISTS v_skill_demand;
DROP VIEW IF EXISTS v_city_jobs;
DROP VIEW IF EXISTS v_experience_salary;
DROP VIEW IF EXISTS v_company_profile;

-- 清场：子表 → 父表
DROP TABLE IF EXISTS dwd_job_skill;
DROP TABLE IF EXISTS dwd_job_welfare;
DROP TABLE IF EXISTS dwd_job_posting;
DROP TABLE IF EXISTS dim_skill;
DROP TABLE IF EXISTS dim_company;
DROP TABLE IF EXISTS ods_job_posting;

CREATE TABLE ods_job_posting (
  run_id        TEXT    NOT NULL,   -- 装载运行标识
  source_row_no INTEGER NOT NULL,   -- 来源行号（分析 CSV 数据行序号，从 1 开始）
  job_id        TEXT    NULL,       -- 岗位 ID（Boss encryptJobId）
  payload_json  TEXT    NOT NULL,   -- 整条岗位记录 JSON（适配层输出，字段不改名）
  source        TEXT    NOT NULL,   -- 数据源标识：boss
  crawled_at    TEXT    NULL,       -- 采集时间
  load_batch    TEXT    NOT NULL,   -- 装载批次（数据文件指纹）
  loaded_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, source_row_no)
);
CREATE INDEX idx_ods_job_id ON ods_job_posting (job_id);

CREATE TABLE dim_company (
  company_key   INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  company_name  TEXT    NOT NULL UNIQUE,   -- 公司名（Boss brandName）
  industry      TEXT    NULL,              -- 所属行业
  finance_stage TEXT    NULL,              -- 融资阶段
  company_size  TEXT    NULL               -- 公司规模标签
);

CREATE TABLE dim_skill (
  skill_key INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  skill     TEXT    NOT NULL UNIQUE        -- 技能名
);

CREATE TABLE dwd_job_posting (
  job_id              TEXT    NOT NULL PRIMARY KEY,
  run_id              TEXT    NOT NULL,
  job_title           TEXT    NULL,
  company_key         INTEGER NULL REFERENCES dim_company (company_key),
  city_key            INTEGER NULL REFERENCES dim_city (city_key),
  industry_key        INTEGER NULL REFERENCES dim_industry (industry_key),
  job_type            TEXT    NULL,        -- 实习 / 校招 / 社招
  experience_rank     INTEGER NOT NULL,    -- 0 应届 → 5 十年以上，9=不限/未知
  company_size_rank   INTEGER NOT NULL,    -- 0 最小 → 5 万人以上，9=未知
  salary_lower        REAL    NULL,
  salary_upper        REAL    NULL,
  salary_months       INTEGER NULL,
  salary_unit         TEXT    NULL,        -- month / day / hour
  salary_mid          REAL    NULL,        -- 月薪中值；解析失败为 NULL
  salary_band_ratio   REAL    NULL,        -- （上界-下界）/中值
  publish_date        TEXT    NULL,        -- 归一后的发布日期
  first_seen_at       TEXT    NULL,
  last_seen_at        TEXT    NULL,
  crawled_at          TEXT    NULL,
  detail_status       TEXT    NULL,        -- ok / empty / 404 / 403 / verify / error / none
  jd_len              INTEGER NULL,
  CHECK (salary_mid IS NULL OR salary_mid > 0)
);
CREATE INDEX idx_job_company    ON dwd_job_posting (company_key);
CREATE INDEX idx_job_city       ON dwd_job_posting (city_key);
CREATE INDEX idx_job_publish    ON dwd_job_posting (publish_date);
CREATE INDEX idx_job_experience ON dwd_job_posting (experience_rank);

CREATE TABLE dwd_job_skill (
  job_id    TEXT    NOT NULL REFERENCES dwd_job_posting (job_id),
  skill_key INTEGER NOT NULL REFERENCES dim_skill (skill_key),
  PRIMARY KEY (job_id, skill_key)
);

CREATE TABLE dwd_job_welfare (
  job_id  TEXT NOT NULL REFERENCES dwd_job_posting (job_id),
  welfare TEXT NOT NULL,
  PRIMARY KEY (job_id, welfare)
);

-- -----------------------------------------------------------------------------
-- 视图（与 MySQL 版同一份口径）
-- -----------------------------------------------------------------------------
CREATE VIEW v_job_market_overview AS
SELECT
  p.run_id,
  COUNT(*)                                                          AS jobs,
  COUNT(DISTINCT p.company_key)                                     AS companies,
  COUNT(DISTINCT p.city_key)                                        AS cities,
  ROUND(AVG(p.salary_mid), 2)                                       AS mean_salary,
  SUM(CASE WHEN p.salary_mid IS NOT NULL THEN 1 ELSE 0 END)         AS jobs_with_salary,
  ROUND(100.0 * SUM(CASE WHEN p.salary_mid IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                                    AS salary_parse_pct,
  SUM(CASE WHEN p.detail_status = 'ok' THEN 1 ELSE 0 END)           AS jobs_with_detail,
  MIN(p.publish_date)                                               AS publish_from,
  MAX(p.publish_date)                                               AS publish_to
FROM dwd_job_posting p
GROUP BY p.run_id;

CREATE VIEW v_skill_demand AS
SELECT
  s.skill,
  COUNT(*)                       AS job_count,
  ROUND(AVG(p.salary_mid), 2)    AS mean_salary
FROM dwd_job_skill js
JOIN dim_skill s        ON s.skill_key = js.skill_key
JOIN dwd_job_posting p  ON p.job_id   = js.job_id
WHERE p.salary_mid IS NOT NULL
GROUP BY s.skill;

CREATE VIEW v_city_jobs AS
SELECT
  c.city,
  COUNT(*)                    AS job_count,
  ROUND(AVG(p.salary_mid), 2) AS mean_salary
FROM dwd_job_posting p
JOIN dim_city c ON c.city_key = p.city_key
WHERE p.salary_mid IS NOT NULL
GROUP BY c.city;

CREATE VIEW v_experience_salary AS
SELECT
  p.experience_rank,
  COUNT(*)                    AS job_count,
  ROUND(AVG(p.salary_mid), 2) AS mean_salary
FROM dwd_job_posting p
WHERE p.salary_mid IS NOT NULL AND p.experience_rank <> 9
GROUP BY p.experience_rank;

CREATE VIEW v_company_profile AS
SELECT
  co.finance_stage,
  co.company_size,
  COUNT(*)                    AS job_count,
  ROUND(AVG(p.salary_mid), 2) AS mean_salary
FROM dwd_job_posting p
JOIN dim_company co ON co.company_key = p.company_key
WHERE p.salary_mid IS NOT NULL
GROUP BY co.finance_stage, co.company_size;