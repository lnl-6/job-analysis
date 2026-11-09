-- =============================================================================
-- schema_sqlite.sql —— 本地零依赖镜像（SQLite 3.25+）
-- =============================================================================
-- 为什么存在这份文件：
--   仓库的权威 schema 是 sql/00_schema.sql（MySQL 8，含 COMMENT / ENGINE / 索引语法）。
--   但评审者、CI 的快速通道、以及没装 MySQL 的机器上，都跑不了 MySQL 8 方言。
--   本文件提供一份**表名、列名、约束语义完全一致**的 SQLite 版本，让下面三件事
--   在本地就能验证：
--     1) sql/01_views.sql 的所有视图（只依赖窗口函数与 CTE，两边通用）
--     2) sql/queries/*.sql 的全部分析查询
--     3) 装载脚本的幂等性（同一份数据重复装载不产生重复行）
--
-- 与 MySQL 版的已知差异（都在“无处安放”的语言能力上，不影响口径）：
--   * 没有 COMMENT：中文含义保留在本文件注释与 docs/data_dictionary.md 里。
--   * 没有独立 DATETIME/JSON 类型：分别用 TEXT 存 ISO 文本与 JSON 文本。
--   * 外键默认不强制：连接层显式 `PRAGMA foreign_keys = ON`（src/db.py 已开）。
--   * AUTO_INCREMENT → INTEGER PRIMARY KEY AUTOINCREMENT。
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- ODS：原始落地层
-- -----------------------------------------------------------------------------
-- ---------------------------------------------------------------------------
-- 清场：按「子表 → 父表」顺序删除。
-- 为什么必须先清场：SQLite 在 PRAGMA foreign_keys=ON 时，DROP TABLE 会删除表内
-- 所有行并触发外键检查 —— 先删被引用的维表会被事实表的外键挡住
-- （IntegrityError: FOREIGN KEY constraint failed）。MySQL 会报 errno 3730。
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS ads_weight_sensitivity;
DROP TABLE IF EXISTS ads_quality_checks;
DROP TABLE IF EXISTS ads_comparisons;
DROP TABLE IF EXISTS ads_major_city_matrix;
DROP TABLE IF EXISTS ads_group_stats;
DROP TABLE IF EXISTS ads_run_meta;
DROP TABLE IF EXISTS dwd_survey_clean;
DROP TABLE IF EXISTS dim_industry;
DROP TABLE IF EXISTS dim_direction;
DROP TABLE IF EXISTS dim_education;
DROP TABLE IF EXISTS dim_city;
DROP TABLE IF EXISTS dim_major;
DROP TABLE IF EXISTS ods_employment_survey;

-- 下面每张表建表前还会各写一次 DROP IF EXISTS（此处已被清掉，属幂等空操作）。
DROP TABLE IF EXISTS ods_employment_survey;
CREATE TABLE ods_employment_survey (
  load_batch       TEXT    NOT NULL,   -- 装载批次（数据文件 SHA-256 前 12 位）
  source_row_no    INTEGER NOT NULL,   -- 数据行号（CSV 第 2 行记为 1）
  respondent_id    TEXT    NULL,       -- 问卷编号（原始 ID 列）
  gender           TEXT    NULL,       -- 性别（原始字符串，缺失保持 NULL）
  major            TEXT    NULL,       -- 专业
  education        TEXT    NULL,       -- 学历
  direction        TEXT    NULL,       -- 毕业去向
  industry         TEXT    NULL,       -- 就业行业
  expected_salary  TEXT    NULL,       -- 期望薪资（原样字符串，清洗在 Python 侧完成）
  city             TEXT    NULL,       -- 工作城市
  satisfaction     TEXT    NULL,       -- 满意度评分（原始字符串）
  has_internship   TEXT    NULL,       -- 是否有实习经历
  loaded_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- 入库时间
  PRIMARY KEY (load_batch, source_row_no)
);
CREATE INDEX idx_ods_major_city ON ods_employment_survey (major, city);

-- -----------------------------------------------------------------------------
-- DWD：维度表
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS dim_major;
CREATE TABLE dim_major (
  major_key  INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  major      TEXT    NOT NULL UNIQUE          -- 专业
);

DROP TABLE IF EXISTS dim_city;
CREATE TABLE dim_city (
  city_key  INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  city      TEXT    NOT NULL UNIQUE           -- 工作城市
);

DROP TABLE IF EXISTS dim_education;
CREATE TABLE dim_education (
  education_key  INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  education      TEXT    NOT NULL UNIQUE,     -- 学历
  education_rank INTEGER NOT NULL             -- 学历序号：1=大专 2=本科 3=硕士 4=博士（排序用）
);

DROP TABLE IF EXISTS dim_direction;
CREATE TABLE dim_direction (
  direction_key  INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  direction      TEXT    NOT NULL UNIQUE      -- 毕业去向
);

DROP TABLE IF EXISTS dim_industry;
CREATE TABLE dim_industry (
  industry_key INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  industry     TEXT    NOT NULL UNIQUE        -- 就业行业
);

-- -----------------------------------------------------------------------------
-- DWD：事实表（粒度 = 一份问卷）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS dwd_survey_clean;
CREATE TABLE dwd_survey_clean (
  respondent_uid        INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,  -- 代理键
  load_batch            TEXT    NOT NULL,   -- 装载批次
  source_row_no         INTEGER NOT NULL,   -- 来源行号（与 ODS 对齐，幂等键的一半）
  respondent_id         TEXT    NULL,       -- 问卷编号
  gender                TEXT    NULL,       -- 性别（缺失已按众数填充）
  major_key             INTEGER NOT NULL REFERENCES dim_major (major_key),
  education_key         INTEGER NOT NULL REFERENCES dim_education (education_key),
  direction_key         INTEGER NOT NULL REFERENCES dim_direction (direction_key),
  city_key              INTEGER NOT NULL REFERENCES dim_city (city_key),
  industry_key          INTEGER NULL REFERENCES dim_industry (industry_key),
  expected_salary       NUMERIC(10,2) NOT NULL CHECK (expected_salary >= 0),
  satisfaction          NUMERIC(3,1)  NOT NULL CHECK (satisfaction >= 0 AND satisfaction <= 5),
  has_internship        INTEGER NOT NULL,   -- 1=是 0=否
  salary_is_filled      INTEGER NOT NULL,   -- 期望薪资是否为填充值
  satisfaction_is_filled INTEGER NOT NULL,  -- 满意度是否为填充值
  loaded_at             TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (load_batch, source_row_no)
);
CREATE INDEX idx_dwd_major     ON dwd_survey_clean (major_key);
CREATE INDEX idx_dwd_city      ON dwd_survey_clean (city_key);
CREATE INDEX idx_dwd_education ON dwd_survey_clean (education_key);
CREATE INDEX idx_dwd_direction ON dwd_survey_clean (direction_key);
CREATE INDEX idx_dwd_salary    ON dwd_survey_clean (expected_salary);

-- -----------------------------------------------------------------------------
-- ADS：分析结果层（统计量只算一次，报告与 BI 都从这里取数）
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS ads_run_meta;
CREATE TABLE ads_run_meta (
  run_id                TEXT    NOT NULL PRIMARY KEY,
  load_batch            TEXT    NOT NULL,
  data_file             TEXT    NOT NULL,
  data_sha256           TEXT    NOT NULL,
  rows_raw              INTEGER NOT NULL,
  rows_clean            INTEGER NOT NULL,
  rows_invalid_salary   INTEGER NOT NULL,
  rows_outlier_removed  INTEGER NOT NULL,
  missing_filled_json   TEXT    NULL,
  min_group_size        INTEGER NOT NULL,
  min_recommend_samples INTEGER NOT NULL,
  ci_level              NUMERIC(4,3) NOT NULL,
  alpha                 NUMERIC(4,3) NOT NULL,
  python_version        TEXT    NULL,
  pandas_version        TEXT    NULL,
  numpy_version         TEXT    NULL,
  scipy_version         TEXT    NULL,
  run_at                TEXT    NOT NULL
);

DROP TABLE IF EXISTS ads_group_stats;
CREATE TABLE ads_group_stats (
  run_id            TEXT    NOT NULL,
  dimension         TEXT    NOT NULL,   -- city / major / education / direction / industry
  group_value       TEXT    NOT NULL,
  sample_size       INTEGER NOT NULL,
  mean_salary       NUMERIC(10,2) NOT NULL,
  median_salary     NUMERIC(10,2) NOT NULL,
  p25               NUMERIC(10,2) NULL,
  p50               NUMERIC(10,2) NULL,
  p75               NUMERIC(10,2) NULL,
  salary_std        NUMERIC(10,2) NULL,
  salary_ci_low     NUMERIC(10,2) NULL,
  salary_ci_high    NUMERIC(10,2) NULL,
  mean_satisfaction NUMERIC(4,2)  NULL,
  internship_rate   NUMERIC(5,2)  NULL,
  sample_ok         INTEGER NOT NULL,
  PRIMARY KEY (run_id, dimension, group_value)
);
CREATE INDEX idx_ads_group_dim ON ads_group_stats (run_id, dimension);

DROP TABLE IF EXISTS ads_major_city_matrix;
CREATE TABLE ads_major_city_matrix (
  run_id        TEXT    NOT NULL,
  major         TEXT    NOT NULL,
  city          TEXT    NOT NULL,
  sample_size   INTEGER NOT NULL,
  mean_salary   NUMERIC(10,2) NULL,   -- 样本量不足时为 NULL（不留小样本均值）
  insufficient  INTEGER NOT NULL,
  PRIMARY KEY (run_id, major, city)
);

DROP TABLE IF EXISTS ads_comparisons;
CREATE TABLE ads_comparisons (
  run_id          TEXT    NOT NULL,
  comparison_key  TEXT    NOT NULL,
  label           TEXT    NOT NULL,
  group_a         TEXT    NOT NULL,
  group_b         TEXT    NOT NULL,
  n_a             INTEGER NOT NULL,
  n_b             INTEGER NOT NULL,
  mean_a          NUMERIC(10,2) NULL,
  mean_b          NUMERIC(10,2) NULL,
  mean_diff       NUMERIC(10,2) NULL,
  ci_low          NUMERIC(10,2) NULL,
  ci_high         NUMERIC(10,2) NULL,
  p_welch         REAL    NULL,
  p_holm          REAL    NULL,
  p_mannwhitney   REAL    NULL,
  cohens_d        REAL    NULL,
  effect_label    TEXT    NULL,
  sufficient      INTEGER NOT NULL,
  significant     INTEGER NOT NULL,
  verdict         TEXT    NOT NULL,
  PRIMARY KEY (run_id, comparison_key)
);

DROP TABLE IF EXISTS ads_quality_checks;
CREATE TABLE ads_quality_checks (
  run_id    TEXT    NOT NULL,
  seq       INTEGER NOT NULL,
  item      TEXT    NOT NULL,
  detail    TEXT    NOT NULL,
  verdict   TEXT    NOT NULL,
  flagged   INTEGER NOT NULL,
  PRIMARY KEY (run_id, seq)
);

DROP TABLE IF EXISTS ads_weight_sensitivity;
CREATE TABLE ads_weight_sensitivity (
  run_id              TEXT    NOT NULL,
  seq                 INTEGER NOT NULL,
  scenario            TEXT    NOT NULL,
  top1_city           TEXT    NULL,
  spearman_vs_default REAL    NULL,
  top1_same           INTEGER NULL,
  note                TEXT    NULL,
  PRIMARY KEY (run_id, seq)
);