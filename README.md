# job-analysis · 招聘岗位数据分析

> 抓 Boss 直聘岗位数据 → 清洗 → 分组统计 + 显著性检验 → 生成报告与图表；附带 MySQL 8 数据层与 Power BI 呈现方案。
> **仓库不携带任何数据文件**，数据由使用者自己抓取或提供。
---

## 一、这个项目解决什么问题

网上讨论「哪个方向薪资高」「要不要实习」「一线城市值不值」时，大多是拍脑袋。这个项目把「招聘岗位」做成一条**可复现、可追溯**的分析流程：

- **采**：低频抓取 Boss 直聘岗位搜索页，原始记录落 JSONL，支持断点续爬
- **洗**：无效薪资剔除 → 缺失填充 → IQR 异常值剔除，每一步都记录数量
- **算**：多维度分组统计 + 成对比较与**显著性检验**（95% 置信区间、Welch t、Mann-Whitney U、Cohen's d、Holm 多重比较校正）
- **出**：一张 Markdown 报告 + 9 张图表；报告带溯源区（数据指纹、阈值、环境、运行时间）
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
pytest -q tests/          # 58 项；或分文件跑 test_pipeline.py / test_sql_layer.py / test_source_boss.py

# 5.（可选）装载进数据层并跑门禁；没有 MySQL 时走 SQLite 镜像
python scripts/load_to_mysql.py --backend sqlite
```
常用参数：`--data <路径>` 指定数据文件、`--check-data` 只检查数据、`--no-charts` 只算数不出图、`--top N` 专业维度取前 N 名。图形界面 `python gui.py`（按样本给城市做描述性排序，不做预测）。

产出：`output/report.md`（报告）、`output/*.png`（9 张图）、`output/employment_analysis.db`（SQLite 镜像）。

## 三、技术栈

`Python 3.10+` · `pandas` / `NumPy` / `SciPy`（统计检验） · `Matplotlib` / `Seaborn`（图表） · `MySQL 8` / `SQLite`（数据层，PyMySQL） · `Power BI`（呈现层） · `pytest` + `ruff` + `GitHub Actions`（CI 跑 Linux 双版本 Python 与 MySQL service container）

## 四、目录结构

```
src/            分析内核：loader / cleaner / analysis / stats / quality / report / viz / recommender / db / sources/boss.py
scripts/        crawl_boss.py（抓取）、load_to_mysql.py（装载+门禁）、export_published_summary.py（聚合表）
sql/            ODS/DWD/ADS 建表 + 视图 + 分析型查询 + 17 项质量门禁（MySQL 8 与 SQLite 双版本）
tests/          58 项离线测试（数据由 tests/_fixtures.py 现场生成）
docs/           口径对照、字段字典、数据源说明、Power BI 指南、方法学与踩坑
data/published/ 可公开的聚合统计（唯一入库的数据文件）
output/         运行产物：报告、图表、SQLite 库（不入库）
```

更多文档：[指标口径](docs/metrics.md) · [字段字典](docs/data_dictionary.md) · [数据源与合规](docs/data_source_boss.md) · [Power BI 模型与 DAX](docs/powerbi.md) · [方法学/踩坑/局限](docs/methods.md)

## 五、部分名词解释

| 列 | 岗位数据的含义 |
| --- | --- |
| `期望薪资` | 岗位薪资区间中值（元/月，`K`/`万`/`元每天` 已统一折算） |
| `满意度评分` | 岗位吸引力**代理分**（0~5，由福利/融资阶段/公司规模加权，**不是满意度**） |
| `专业` | 岗位方向（按标题关键词归类：数据分析/数据开发/算法…） |
| `毕业去向` | 岗位类型（实习 / 校招 / 社招） |

报告溯源区会打印「数据口径」与数据指纹（SHA-256），便于核对这批数字来自哪次运行。字段映射、薪资解析规则、代理分公式、合规边界见 [`docs/data_source_boss.md`](docs/data_source_boss.md)；聚合表字段见 [`data/published/README.md`](data/published/README.md)。

## License

MIT，见 [LICENSE](LICENSE)。