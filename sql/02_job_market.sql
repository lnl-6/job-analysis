-- =============================================================================
-- 02_job_market.sql —— 岗位市场层（Boss 直聘数据源，MySQL 8.0）
-- =============================================================================
-- 为什么单独一个文件：
--   00_schema.sql 描述的是「一份问卷 → 一行」的就业调查数据模型。
--   岗位数据（一个岗位 → 一行）有自己的一套维度和多值字段（技能 / 福利），
--   硬塞进问卷事实表会把 7 列口径搅浑。这里用一条**并行的支路**承载它，
--   共用 dim_city / dim_industry，其余维度（公司 / 技能）新增。
--
-- 分层约定（与问卷侧一致）：
--   ods_job_posting   原始层：适配层解析后的整条岗位记录（JSON），不改字段
--   dwd_job_posting   事实层：一岗一行，薪资 / 序数 / 发布时间都已归一
--   dwd_job_skill     多值桥表：岗位 × 技能
--   dwd_job_welfare   多值桥表：岗位 × 福利
--   视图              BI 与 SQL 查询的取数口径（不在 SQL 里重算统计量）
--
-- 执行顺序：先 00_schema.sql → 再 02_job_market.sql（依赖 dim_city / dim_industry）。
-- 装载：src/db.py 的 _load_job_layer 负责写这张表（幂等 upsert，job_id 为主键）。
-- =============================================================================

SET NAMES utf8mb4;
USE employment_analysis;

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
  run_id        VARCHAR(40)  NOT NULL COMMENT '装载运行标识（与 ads_run_meta.run_id 一致）',
  source_row_no INT          NOT NULL COMMENT '来源行号（分析 CSV 的数据行序号，从 1 开始）',
  job_id        VARCHAR(128) NULL     COMMENT '岗位 ID（Boss 的 encryptJobId）',
  payload_json  LONGTEXT     NOT NULL COMMENT '整条岗位记录（适配层输出，字段不改名）',
  source        VARCHAR(16)  NOT NULL COMMENT '数据源标识：boss',
  crawled_at    VARCHAR(32)  NULL     COMMENT '采集时间',
  load_batch    VARCHAR(40)  NOT NULL COMMENT '装载批次（数据文件指纹）',
  loaded_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (run_id, source_row_no),
  KEY idx_ods_job_id (job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ODS：岗位原始记录（一行一个岗位，JSON 保真）';

CREATE TABLE dim_company (
  company_key   INT           NOT NULL AUTO_INCREMENT COMMENT '公司代理键',
  company_name  VARCHAR(200)  NOT NULL COMMENT '公司名（Boss brandName）',
  industry      VARCHAR(64)   NULL     COMMENT '所属行业（Boss brandIndustry）',
  finance_stage VARCHAR(64)   NULL     COMMENT '融资阶段（Boss brandStageName）',
  company_size  VARCHAR(64)   NULL     COMMENT '公司规模标签（Boss brandScaleName）',
  PRIMARY KEY (company_key),
  UNIQUE KEY uk_company_name (company_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='维度：招聘公司（岗位数 / 融资阶段 / 规模的切片基准）';

CREATE TABLE dim_skill (
  skill_key INT         NOT NULL AUTO_INCREMENT COMMENT '技能代理键',
  skill     VARCHAR(64) NOT NULL COMMENT '技能名（Boss skills 字段里的单个值）',
  PRIMARY KEY (skill_key),
  UNIQUE KEY uk_skill (skill)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='维度：技能（多值字段只有拆成桥表才能聚合）';

CREATE TABLE dwd_job_posting (
  job_id              VARCHAR(128)  NOT NULL COMMENT '岗位 ID（Boss encryptJobId）',
  run_id              VARCHAR(40)   NOT NULL COMMENT '装载运行标识',
  job_title           VARCHAR(200)  NULL     COMMENT '岗位名称',
  company_key         INT           NULL     COMMENT '公司代理键',
  city_key            INT           NULL     COMMENT '城市代理键（共用 dim_city）',
  industry_key        INT           NULL     COMMENT '行业代理键（共用 dim_industry）',
  job_type            VARCHAR(16)   NULL     COMMENT '岗位类型：实习 / 校招 / 社招',
  experience_rank     TINYINT       NOT NULL COMMENT '经验序数：0 应届 → 5 十年以上，9=不限/未知',
  company_size_rank   TINYINT       NOT NULL COMMENT '规模序数：0 最小 → 5 万人以上，9=未知',
  salary_lower        DECIMAL(10,2) NULL     COMMENT '月薪下界（元）',
  salary_upper        DECIMAL(10,2) NULL     COMMENT '月薪上界（元）',
  salary_months       TINYINT       NULL     COMMENT '年薪月数（默认 12）',
  salary_unit         VARCHAR(8)    NULL     COMMENT '原始时间单位：month / day / hour',
  salary_mid          DECIMAL(10,2) NULL     COMMENT '月薪中值（元）；解析失败为 NULL',
  salary_band_ratio   DECIMAL(6,4)  NULL     COMMENT '薪资带宽 =（上界-下界）/中值',
  publish_date        DATE          NULL     COMMENT '岗位发布时间（归一成日期；推不出来为 NULL）',
  first_seen_at       DATETIME      NULL     COMMENT '首次采集到的时间',
  last_seen_at        DATETIME      NULL     COMMENT '最近一次采集到的时间（多轮采集才有意义）',
  crawled_at          VARCHAR(32)   NULL     COMMENT '本条记录采集时间',
  detail_status       VARCHAR(16)   NULL     COMMENT 'JD 抓取状态：ok / empty / 404 / 403 / verify / error / none',
  jd_len              INT           NULL     COMMENT 'JD 文本长度（0 表示没抓到）',
  PRIMARY KEY (job_id),
  KEY idx_job_company (company_key),
  KEY idx_job_city (city_key),
  KEY idx_job_publish (publish_date),
  KEY idx_job_experience (experience_rank),
  CONSTRAINT fk_job_company  FOREIGN KEY (company_key)  REFERENCES dim_company (company_key),
  CONSTRAINT fk_job_city     FOREIGN KEY (city_key)     REFERENCES dim_city (city_key),
  CONSTRAINT fk_job_industry FOREIGN KEY (industry_key) REFERENCES dim_industry (industry_key),
  CONSTRAINT ck_job_salary   CHECK (salary_mid IS NULL OR salary_mid > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='DWD：岗位事实表（一岗一行，薪资与序数已归一）';

CREATE TABLE dwd_job_skill (
  job_id    VARCHAR(128) NOT NULL COMMENT '岗位 ID',
  skill_key INT          NOT NULL COMMENT '技能代理键',
  PRIMARY KEY (job_id, skill_key),
  CONSTRAINT fk_jobskill_job   FOREIGN KEY (job_id)    REFERENCES dwd_job_posting (job_id),
  CONSTRAINT fk_jobskill_skill FOREIGN KEY (skill_key) REFERENCES dim_skill (skill_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='DWD：岗位 × 技能（多值字段的桥表）';

CREATE TABLE dwd_job_welfare (
  job_id  VARCHAR(128) NOT NULL COMMENT '岗位 ID',
  welfare VARCHAR(64)  NOT NULL COMMENT '福利标签（五险一金 / 双休 …）',
  PRIMARY KEY (job_id, welfare),
  CONSTRAINT fk_jobwelfare_job FOREIGN KEY (job_id) REFERENCES dwd_job_posting (job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='DWD：岗位 × 福利（多值字段的桥表）';

-- -----------------------------------------------------------------------------
-- 视图：BI/SQL 取数口径（只做聚合与连接，不重算 Python 侧的统计量）
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_job_market_overview AS
SELECT
  p.run_id,
  COUNT(*)                                                          AS jobs,
  COUNT(DISTINCT p.company_key)                                     AS companies,
  COUNT(DISTINCT p.city_key)                                        AS cities,
  ROUND(AVG(p.salary_mid), 2)                                       AS mean_salary,
  SUM(CASE WHEN p.salary_mid IS NOT NULL THEN 1 ELSE 0 END)          AS jobs_with_salary,
  ROUND(100.0 * SUM(CASE WHEN p.salary_mid IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                                    AS salary_parse_pct,
  SUM(CASE WHEN p.detail_status = 'ok' THEN 1 ELSE 0 END)           AS jobs_with_detail,
  MIN(p.publish_date)                                               AS publish_from,
  MAX(p.publish_date)                                               AS publish_to
FROM dwd_job_posting p
GROUP BY p.run_id;

CREATE OR REPLACE VIEW v_skill_demand AS
SELECT
  s.skill,
  COUNT(*)                       AS job_count,
  ROUND(AVG(p.salary_mid), 2)    AS mean_salary
FROM dwd_job_skill js
JOIN dim_skill s        ON s.skill_key = js.skill_key
JOIN dwd_job_posting p  ON p.job_id   = js.job_id
WHERE p.salary_mid IS NOT NULL
GROUP BY s.skill;

CREATE OR REPLACE VIEW v_city_jobs AS
SELECT
  c.city,
  COUNT(*)                                  AS job_count,
  ROUND(AVG(p.salary_mid), 2)                AS mean_salary
FROM dwd_job_posting p
JOIN dim_city c ON c.city_key = p.city_key
WHERE p.salary_mid IS NOT NULL
GROUP BY c.city;

CREATE OR REPLACE VIEW v_experience_salary AS
SELECT
  p.experience_rank,
  COUNT(*)                    AS job_count,
  ROUND(AVG(p.salary_mid), 2) AS mean_salary
FROM dwd_job_posting p
WHERE p.salary_mid IS NOT NULL AND p.experience_rank <> 9
GROUP BY p.experience_rank;

CREATE OR REPLACE VIEW v_company_profile AS
SELECT
  co.finance_stage,
  co.company_size,
  COUNT(*)                    AS job_count,
  ROUND(AVG(p.salary_mid), 2) AS mean_salary
FROM dwd_job_posting p
JOIN dim_company co ON co.company_key = p.company_key
WHERE p.salary_mid IS NOT NULL
GROUP BY co.finance_stage, co.company_size;