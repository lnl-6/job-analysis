-- =============================================================================
-- 就业数据分析项目 —— MySQL 8.0 数据层
-- =============================================================================
-- 分层约定
--   ODS（原始落地）  ：原样追加，不做任何清洗，保留原始字符串
--   DWD（清洗明细）  ：Python 清洗后的明细，粒度 = 一份问卷，星型模型的事实表
--   ADS（分析结果）  ：由 Python 统计层算好后写入的结果表（报告里每一块数字的来源）
--
-- 命名约定
--   * 表名 / 列名一律英文 snake_case，中文含义写在 COMMENT 里
--     —— COMMENT 就是数据字典的单一来源，改列先改注释
--   * 所有表都有唯一键，装载脚本用 INSERT ... ON DUPLICATE KEY UPDATE 保证幂等
--   * 事实表的幂等键是 (load_batch, source_row_no)：同一份文件重复装载不会翻倍
--
-- 用法
--   mysql -h 127.0.0.1 -u analyst -p < sql/00_schema.sql
--   或 docker compose up -d（容器首次启动会自动执行本文件，见 docker-compose.yml）
-- =============================================================================

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS employment_analysis
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE employment_analysis;

-- =============================================================================
-- ODS：原始落地层
-- =============================================================================
-- ---------------------------------------------------------------------------
-- 清场：按「子表 → 父表」顺序删除。
-- 为什么必须先清场：被外键引用的表不能先删 —— MySQL 报 errno 3730
-- （Cannot drop table referenced by a foreign key constraint），
-- SQLite 在 foreign_keys=ON 时报 FOREIGN KEY constraint failed。
-- 所以先删事实表与结果表，再删维表。
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
  load_batch       VARCHAR(32)  NOT NULL COMMENT '装载批次（数据文件 SHA-256 前 12 位）',
  source_row_no    INT          NOT NULL COMMENT '数据行号（CSV 第 2 行记为 1）',
  respondent_id    VARCHAR(32)  NULL     COMMENT '问卷编号（原始 ID 列）',
  gender           VARCHAR(16)  NULL     COMMENT '性别（原始字符串，缺失保持 NULL）',
  major            VARCHAR(64)  NULL     COMMENT '专业',
  education        VARCHAR(16)  NULL     COMMENT '学历',
  direction        VARCHAR(32)  NULL     COMMENT '毕业去向',
  industry         VARCHAR(64)  NULL     COMMENT '就业行业',
  expected_salary  VARCHAR(32)  NULL     COMMENT '期望薪资（原样字符串，清洗在 Python 侧完成）',
  city             VARCHAR(32)  NULL     COMMENT '工作城市',
  satisfaction     VARCHAR(32)  NULL     COMMENT '满意度评分（原始字符串）',
  has_internship   VARCHAR(16)  NULL     COMMENT '是否有实习经历',
  loaded_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (load_batch, source_row_no),
  KEY idx_ods_major_city (major, city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ODS：就业调查问卷原始落地（只追加，不修改）';

-- =============================================================================
-- DWD：维度表（星型模型的维）
-- =============================================================================
DROP TABLE IF EXISTS dim_major;
CREATE TABLE dim_major (
  major_key  INT         NOT NULL AUTO_INCREMENT,
  major      VARCHAR(64) NOT NULL COMMENT '专业',
  PRIMARY KEY (major_key),
  UNIQUE KEY uk_dim_major (major)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='维表：专业';

DROP TABLE IF EXISTS dim_city;
CREATE TABLE dim_city (
  city_key  INT         NOT NULL AUTO_INCREMENT,
  city      VARCHAR(32) NOT NULL COMMENT '工作城市',
  PRIMARY KEY (city_key),
  UNIQUE KEY uk_dim_city (city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='维表：工作城市';

DROP TABLE IF EXISTS dim_education;
CREATE TABLE dim_education (
  education_key  INT         NOT NULL AUTO_INCREMENT,
  education      VARCHAR(16) NOT NULL COMMENT '学历',
  education_rank TINYINT     NOT NULL COMMENT '学历序号：1=大专 2=本科 3=硕士 4=博士（排序用）',
  PRIMARY KEY (education_key),
  UNIQUE KEY uk_dim_education (education)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='维表：学历';

DROP TABLE IF EXISTS dim_direction;
CREATE TABLE dim_direction (
  direction_key  INT         NOT NULL AUTO_INCREMENT,
  direction      VARCHAR(32) NOT NULL COMMENT '毕业去向',
  PRIMARY KEY (direction_key),
  UNIQUE KEY uk_dim_direction (direction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='维表：毕业去向';

DROP TABLE IF EXISTS dim_industry;
CREATE TABLE dim_industry (
  industry_key INT         NOT NULL AUTO_INCREMENT,
  industry     VARCHAR(64) NOT NULL COMMENT '就业行业',
  PRIMARY KEY (industry_key),
  UNIQUE KEY uk_dim_industry (industry)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='维表：就业行业';

-- =============================================================================
-- DWD：事实表（粒度 = 一份问卷）
-- =============================================================================
DROP TABLE IF EXISTS dwd_survey_clean;
CREATE TABLE dwd_survey_clean (
  respondent_uid       BIGINT        NOT NULL AUTO_INCREMENT COMMENT '代理键',
  load_batch           VARCHAR(32)   NOT NULL COMMENT '装载批次（数据文件 SHA-256 前 12 位）',
  source_row_no        INT           NOT NULL COMMENT '来源行号（与 ODS 对齐，幂等键的一半）',
  respondent_id        VARCHAR(32)   NULL     COMMENT '问卷编号',
  gender               VARCHAR(8)    NULL     COMMENT '性别（缺失已按众数填充）',
  major_key            INT           NOT NULL COMMENT '→ dim_major.major_key',
  education_key        INT           NOT NULL COMMENT '→ dim_education.education_key',
  direction_key        INT           NOT NULL COMMENT '→ dim_direction.direction_key',
  city_key             INT           NOT NULL COMMENT '→ dim_city.city_key',
  industry_key         INT           NULL     COMMENT '→ dim_industry.industry_key',
  expected_salary      DECIMAL(10,2) NOT NULL COMMENT '期望薪资（元）：≥ 0，缺失按中位数填充',
  satisfaction         DECIMAL(3,1)  NOT NULL COMMENT '满意度评分（0~5，缺失按中位数填充）',
  has_internship       TINYINT(1)    NOT NULL COMMENT '是否有实习经历：1=是 0=否',
  salary_is_filled     TINYINT(1)    NOT NULL COMMENT '期望薪资是否为填充值：1=填充 0=原始',
  satisfaction_is_filled TINYINT(1)  NOT NULL COMMENT '满意度是否为填充值：1=填充 0=原始',
  loaded_at            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (respondent_uid),
  UNIQUE KEY uk_dwd_batch_row (load_batch, source_row_no),
  KEY idx_dwd_major (major_key),
  KEY idx_dwd_city (city_key),
  KEY idx_dwd_education (education_key),
  KEY idx_dwd_direction (direction_key),
  KEY idx_dwd_salary (expected_salary),
  CONSTRAINT fk_dwd_major     FOREIGN KEY (major_key)     REFERENCES dim_major (major_key),
  CONSTRAINT fk_dwd_city      FOREIGN KEY (city_key)      REFERENCES dim_city (city_key),
  CONSTRAINT fk_dwd_education FOREIGN KEY (education_key) REFERENCES dim_education (education_key),
  CONSTRAINT fk_dwd_direction FOREIGN KEY (direction_key) REFERENCES dim_direction (direction_key),
  CONSTRAINT fk_dwd_industry  FOREIGN KEY (industry_key)  REFERENCES dim_industry (industry_key),
  CONSTRAINT ck_dwd_salary     CHECK (expected_salary >= 0),
  CONSTRAINT ck_dwd_satisfaction CHECK (satisfaction >= 0 AND satisfaction <= 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='DWD：清洗后的就业调查明细（事实表，粒度 = 一份问卷）';

-- =============================================================================
-- ADS：分析结果层（由 src/stats.py + src/analysis.py 算出后写入）
--   原则：统计量只算一次。报告与 Power BI 都从这里取数，任何一端都不重算。
-- =============================================================================
DROP TABLE IF EXISTS ads_run_meta;
CREATE TABLE ads_run_meta (
  run_id                VARCHAR(40)   NOT NULL COMMENT '运行标识（数据 SHA-256 前 12 位 + 时间戳）',
  load_batch            VARCHAR(32)   NOT NULL COMMENT '装载批次',
  data_file             VARCHAR(255)  NOT NULL COMMENT '数据文件路径',
  data_sha256           CHAR(64)      NOT NULL COMMENT '数据文件 SHA-256（完整值）',
  rows_raw              INT           NOT NULL COMMENT '原始行数',
  rows_clean            INT           NOT NULL COMMENT '清洗后行数',
  rows_invalid_salary   INT           NOT NULL COMMENT '因期望薪资 ≤ 0 被剔除的行数',
  rows_outlier_removed  INT           NOT NULL COMMENT 'IQR 剔除的异常值行数',
  missing_filled_json   JSON          NULL     COMMENT '各列填充数量',
  min_group_size        INT           NOT NULL COMMENT '样本量门槛（两组对比与交叉表）',
  min_recommend_samples INT           NOT NULL COMMENT '推荐候选城市的最小样本量门槛',
  ci_level              DECIMAL(4,3)  NOT NULL COMMENT '置信水平（如 0.950）',
  alpha                 DECIMAL(4,3)  NOT NULL COMMENT '显著性水平（如 0.050）',
  python_version        VARCHAR(32)   NULL     COMMENT '运行环境：Python',
  pandas_version        VARCHAR(32)   NULL     COMMENT '运行环境：pandas',
  numpy_version         VARCHAR(32)   NULL     COMMENT '运行环境：NumPy',
  scipy_version         VARCHAR(32)   NULL     COMMENT '运行环境：SciPy',
  run_at                DATETIME      NOT NULL COMMENT '本次运行时间',
  PRIMARY KEY (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ADS：每次运行的溯源信息（数据指纹 / 阈值 / 依赖版本）';

DROP TABLE IF EXISTS ads_group_stats;
CREATE TABLE ads_group_stats (
  run_id            VARCHAR(40)   NOT NULL COMMENT '运行标识',
  dimension         VARCHAR(16)   NOT NULL COMMENT '维度：city / major / education / direction / industry',
  group_value       VARCHAR(64)   NOT NULL COMMENT '该维度的取值',
  sample_size       INT           NOT NULL COMMENT '样本量',
  mean_salary       DECIMAL(10,2) NOT NULL COMMENT '平均期望薪资（元）',
  median_salary     DECIMAL(10,2) NOT NULL COMMENT '期望薪资中位数',
  p25               DECIMAL(10,2) NULL     COMMENT '期望薪资 25 分位（线性插值）',
  p50               DECIMAL(10,2) NULL     COMMENT '期望薪资 50 分位（线性插值）',
  p75               DECIMAL(10,2) NULL     COMMENT '期望薪资 75 分位（线性插值）',
  salary_std        DECIMAL(10,2) NULL     COMMENT '期望薪资标准差（样本标准差）',
  salary_ci_low     DECIMAL(10,2) NULL     COMMENT '均值的 95% 置信区间下界（Welch）',
  salary_ci_high    DECIMAL(10,2) NULL     COMMENT '均值的 95% 置信区间上界',
  mean_satisfaction DECIMAL(4,2)  NULL     COMMENT '平均满意度评分',
  internship_rate   DECIMAL(5,2)  NULL     COMMENT '有实习经历占比（%）',
  sample_ok         TINYINT(1)    NOT NULL COMMENT '样本量是否达到门槛：1=达标 0=不足（不足时不给结论）',
  PRIMARY KEY (run_id, dimension, group_value),
  KEY idx_ads_group_dim (run_id, dimension)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ADS：各维度的分组统计（维度合并成一张长表，便于 BI 切片）';

DROP TABLE IF EXISTS ads_major_city_matrix;
CREATE TABLE ads_major_city_matrix (
  run_id        VARCHAR(40)   NOT NULL COMMENT '运行标识',
  major         VARCHAR(64)   NOT NULL COMMENT '专业',
  city          VARCHAR(32)   NOT NULL COMMENT '城市',
  sample_size   INT           NOT NULL COMMENT '该专业在该城市的样本量',
  mean_salary   DECIMAL(10,2) NULL     COMMENT '平均期望薪资；样本量不足时为 NULL（不留小样本均值）',
  insufficient  TINYINT(1)    NOT NULL COMMENT '是否因样本量不足被置空：1=是 0=否',
  PRIMARY KEY (run_id, major, city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ADS：专业 × 城市 交叉矩阵（含样本量，供热力图与 BI 矩阵视觉对象使用）';

DROP TABLE IF EXISTS ads_comparisons;
CREATE TABLE ads_comparisons (
  run_id          VARCHAR(40)   NOT NULL COMMENT '运行标识',
  comparison_key  VARCHAR(32)   NOT NULL COMMENT '对比标识（如 edu_phd_vs_bachelor）',
  label           VARCHAR(64)   NOT NULL COMMENT '对比名称（如 学历：博士 vs 本科）',
  group_a         VARCHAR(64)   NOT NULL COMMENT 'A 组名称',
  group_b         VARCHAR(64)   NOT NULL COMMENT 'B 组名称',
  n_a             INT           NOT NULL COMMENT 'A 组样本量',
  n_b             INT           NOT NULL COMMENT 'B 组样本量',
  mean_a          DECIMAL(10,2) NULL     COMMENT 'A 组均值',
  mean_b          DECIMAL(10,2) NULL     COMMENT 'B 组均值',
  mean_diff       DECIMAL(10,2) NULL     COMMENT '均值差（A − B）',
  ci_low          DECIMAL(10,2) NULL     COMMENT '均值差 95% 置信区间下界',
  ci_high         DECIMAL(10,2) NULL     COMMENT '均值差 95% 置信区间上界',
  p_welch         DOUBLE        NULL     COMMENT 'Welch t 检验 p 值（原始）',
  p_holm          DOUBLE        NULL     COMMENT 'Holm 校正后 p 值（判定用这个）',
  p_mannwhitney   DOUBLE        NULL     COMMENT 'Mann-Whitney U 检验 p 值（非参数交叉验证）',
  cohens_d        DOUBLE        NULL     COMMENT 'Cohen''s d 效应量',
  effect_label    VARCHAR(16)   NULL     COMMENT '效应量档位：可忽略 / 小 / 中 / 大',
  sufficient      TINYINT(1)    NOT NULL COMMENT '两组样本量是否都达门槛：1=是 0=否',
  significant     TINYINT(1)    NOT NULL COMMENT '判定结果：1=差异显著（Holm 校正后）0=不显著',
  verdict         VARCHAR(32)   NOT NULL COMMENT '结论文案（样本不足时写明样本不足）',
  PRIMARY KEY (run_id, comparison_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ADS：假设检验结果（置信区间 / p 值 / 效应量 / Holm 校正 / 判定）';

DROP TABLE IF EXISTS ads_quality_checks;
CREATE TABLE ads_quality_checks (
  run_id    VARCHAR(40)  NOT NULL COMMENT '运行标识',
  seq       INT          NOT NULL COMMENT '体检项序号',
  item      VARCHAR(64)  NOT NULL COMMENT '体检项（如 满意度评分 · 组间解释力）',
  detail    TEXT         NOT NULL COMMENT '体检结果描述（含具体数值）',
  verdict   VARCHAR(64)  NOT NULL COMMENT '判定（以「提示」开头的项会被报告点名）',
  flagged   TINYINT(1)   NOT NULL COMMENT '是否被判为需要提示：1=是 0=否',
  PRIMARY KEY (run_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ADS：数据质量体检结果（缺失率 / 组间解释力 eta² / 重复行 / 常量列）';

DROP TABLE IF EXISTS ads_weight_sensitivity;
CREATE TABLE ads_weight_sensitivity (
  run_id               VARCHAR(40)  NOT NULL COMMENT '运行标识',
  seq                  INT          NOT NULL COMMENT '场景序号',
  scenario             VARCHAR(64)  NOT NULL COMMENT '权重场景名称',
  top1_city            VARCHAR(32)  NULL     COMMENT '该场景下的 TOP1 城市',
  spearman_vs_default  DOUBLE       NULL     COMMENT '与默认权重排序的 Spearman 秩相关（无法计算为 NULL）',
  top1_same            TINYINT(1)   NULL     COMMENT 'TOP1 是否与默认场景一致：1=是 0=否',
  note                 VARCHAR(64)  NULL     COMMENT '备注（样本不足等）',
  PRIMARY KEY (run_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ADS：权重敏感性分析（结论对主观权重的依赖程度）';

-- =============================================================================
-- 数据字典：直接从库里读出「表 + 列 + 类型 + 中文含义」
--   SELECT table_name, column_name, column_type, column_comment
--   FROM information_schema.columns
--   WHERE table_schema = 'employment_analysis'
--   ORDER BY table_name, ordinal_position;
-- =============================================================================