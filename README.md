# job-analysis · 招聘岗位数据分析

> 抓 Boss 直聘岗位数据 → 清洗 → 分组统计 + 显著性检验 → 生成报告与图表；附带 MySQL 8 数据层与 Power BI 呈现方案。
> **仓库不携带任何数据文件**，数据由使用者自己抓取或提供。
---

## 一、这个项目解决什么问题

网上讨论「哪个方向薪资高」「要不要实习」「一线城市值不值」时，大多是拍脑袋。这个项目把「招聘岗位」做成一条**可复现、可追溯**的分析流程：

- **采**：低频抓取 Boss 直聘岗位搜索页，原始记录落 JSONL，支持断点续爬
- **洗**：无效薪资剔除 → 缺失填充 → IQR 异常值剔除，每一步都记录数量
- **算**：多维度分组统计 + 成对比较与**显著性检验**（95% 置信区间、Welch t、Mann-Whitney U、Cohen's d、Holm 多重比较校正）
- **出**：一张 Markdown 报告 + 9 张图表；溯源区含数据指纹、阈值、环境、运行时间，以及**抽样完整度 / 方向归类覆盖率 / 薪资可解析占比**；正文单列「维度样本缺口」与「混杂与不比较」两节
- **存**：MySQL 8 三层建模（ODS / DWD / ADS）+ 17 项阻断式数据质量门禁，SQLite 镜像可本地跑
- **呈现**：Power BI 关系模型与 DAX 度量值（只消费结果表，不重算对外数字）

三条贯穿全局的规则：**不预置结论**（每个数字当次现算）、**样本不足不给结论**（< 30 只报样本量）、**不做预测**（只回答「这批样本里两组差多少、有多不确定」）。

可复现性、踩过的坑、已知局限见 [`docs/methods.md`](docs/methods.md)。

## 二、快速开始

需要 Python ≥ 3.10；依赖 `pip install -r requirements.txt`（含 scipy）。GUI 用标准库 tkinter（Linux 需 `python3-tk`）。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 准备数据。例如直接抓取并适配成分析 CSV：
python scripts/crawl_boss.py --cookie-file cookie.txt --query 数据分析 --to-csv

# 3. 跑完整分析 → output/report.md + output/*.png
python main.py

# 4. 测试（不需要任何数据文件）
pytest -q tests/          # 75 项；或分文件跑 test_pipeline.py / test_sql_layer.py / test_source_boss.py / test_actions.py

# 5.（可选）装载进数据层并跑门禁；没有 MySQL 时走 SQLite 镜像
python scripts/load_to_mysql.py --backend sqlite
```
常用参数：`--data <路径>` 指定数据文件、`--check-data` 只检查数据、`--no-charts` 只算数不出图、`--top N` 专业维度取前 N 名。图形界面 `python gui.py` 是**演示用的小工具**（按样本给城市做描述性排序、不做预测），不属于分析主线；主线是 `main.py` 与 SQL/BI 两层。

产出：`output/report.md`（报告）、`output/report.json`（机器可读：溯源 + 对比 + 行动卡）、`output/*.png`（9 张图）、`output/employment_analysis.db`（SQLite 镜像）。

## 三、部分名词解释

| 列 | 岗位数据的含义 |
| --- | --- |
| `期望薪资` | 岗位薪资区间中值（元/月，`K`/`万`/`元每天` 已统一折算） |
| `满意度评分` | 岗位吸引力**代理分**（0~5，由福利/融资阶段/公司规模加权，**不是满意度**） |
| `专业` | 岗位方向（按标题关键词归类：数据分析/数据开发/算法…） |
| `毕业去向` | 岗位类型（实习 / 校招 / 社招） |

报告溯源区会打印「数据口径」与数据指纹（SHA-256），便于核对这批数字来自哪次运行。字段映射、薪资解析规则、代理分公式、合规边界见 [`docs/data_source_boss.md`](docs/data_source_boss.md)；聚合表字段见 [`data/published/README.md`](data/published/README.md)。

## 四、技术栈

`Python 3.10+` · `pandas` / `NumPy` / `SciPy`（统计检验） · `Matplotlib` / `Seaborn`（图表） · `MySQL 8` / `SQLite`（数据层，PyMySQL） · `Power BI`（呈现层，仓库附 `docs/report.html` 作无 BI 环境的替代） · `pytest` + `pytest-cov` + `ruff` + `mypy` + `pre-commit` + `Docker` + `GitHub Actions`（CI：Linux 双版本 Python + mypy 类型检查、MySQL service container、**Windows + GBK** 三个 job）

## 五、目录结构

```
src/            分析内核：loader / cleaner / analysis / stats / quality / actions / report / viz / recommender / db / sources/boss.py
scripts/        crawl_boss.py（抓取）、load_to_mysql.py（装载+门禁）、export_published_summary.py（聚合表 + 按日快照）、audit_major_coverage.py（只读审计归类覆盖率）、compare_snapshots.py（跨日快照对比）
sql/            ODS/DWD/ADS 建表 + 视图 + 分析型查询 + 17 项质量门禁（MySQL 8 与 SQLite 双版本）
tests/          75 项离线测试（数据由 tests/_fixtures.py 现场生成）
docs/           口径对照、字段字典、数据源说明、Power BI 指南、方法学与踩坑、报告快照
data/published/ 可公开的聚合统计（唯一入库的数据文件）
output/         运行产物：报告、图表、SQLite 库（不入库）
```

更多文档：[指标口径](docs/metrics.md) · [字段字典](docs/data_dictionary.md) · [数据源与合规](docs/data_source_boss.md) · [Power BI 模型与 DAX](docs/powerbi.md) · [方法学/踩坑/局限](docs/methods.md) · [报告快照（一次真实运行的结果）](docs/sample_report.md) · [浏览器版报告](docs/report.html)

## 六、行动建议是怎么来的

报告第六节的 7 张行动卡全部由 `src/actions.py` 从 `results` 里**机械推导**，没有任何模型或外推。映射规则：

| 卡片 | 输入 | 怎么判强度 |
| --- | --- | --- |
| 投递方向 | 各方向 P50 与样本量（`ads_group_stats`） | P50 高于整体 10% 且 n ≥ 30 → 强；高于整体 → 中；否则弱 |
| 投递城市 | 机会指数 = 样本占比 ×（P50 / 整体 P50） | 指数 ≥ 0.08 → 中，否则弱 |
| 一线 vs 新一线 | `ads_comparisons.city_tier1_vs_new_tier1` | Holm 显著且 \|d\| ≥ 0.5 → 强；≥ 0.2 → 中；不显著 → 弱 |
| 学历回报 | 相邻学历档 P50 差 + `edu_*` 对比 | 显著且 \|d\| 大 → 强；档位差 < 500 元 → 中（「学历不是主要变量」） |
| 实习优先 | 按岗位类型分层后的实习/非实习差 | 差 ≥ 1000 元 → 中，否则弱 |
| 城市稳健性 | 6 组权重场景中 TOP1 变化次数 | 0 次 → 强；1 次 → 中；≥ 2 次 → 弱 |
| 结论可信度 | 缺口分组数、体检提示、归类覆盖率、配额完成率 | 无缺项 → 强；有样本缺口 → 中；覆盖率/配额不达标 → 弱 |

同一套卡片也会写进 `output/report.json` 的 `actions` 字段，CI 与 BI 可以复用；卡片的生成规则由 `tests/test_actions.py` 守着（空 results 必须产出空列表 —— 防止有人把结论写死）。

## License

MIT，见 [LICENSE](LICENSE)。