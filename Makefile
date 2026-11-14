# 常用命令（Linux / macOS / CI；Windows 下可用 Git Bash，或直接跑 README 里的 PowerShell 命令）
.PHONY: help install test lint run crawl publish sqlite mysql-up mysql-load mysql-down clean

help:  ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install:  ## 安装依赖（含测试与静态检查）
	python -m pip install -r requirements-dev.txt

test:  ## 跑全部测试（分析流水线 + SQL 数据层 + 数据源适配 + 公开聚合表）
	python tests/test_pipeline.py
	python tests/test_sql_layer.py
	python tests/test_source_boss.py
	python tests/test_published_summary.py

lint:  ## 静态检查（未使用导入、未定义名称等「确定是问题」的规则）
	ruff check .

run:  ## 跑完整分析（Boss 岗位数据优先、演示数据兜底），生成报告与图表
	python main.py

crawl:  ## 抓取真实 Boss 岗位数据并适配成分析 CSV（需要先设置 BOSS_COOKIE）
	python scripts/crawl_boss.py --query 数据分析,数据开发 --city 北京,上海 --rounds 3 --to-csv

publish:  ## 导出可公开入库的聚合统计表（data/published/，不含岗位级记录）
	python scripts/export_published_summary.py

sqlite:  ## 装载进本地 SQLite 镜像并跑门禁（零依赖，没装 MySQL 也能验证 SQL 层）
	python scripts/load_to_mysql.py --query 03_pareto_distribution

mysql-up:  ## 起 MySQL 8 容器（首次启动自动执行 00_schema.sql 与 01_views.sql）
	docker compose up -d mysql

mysql-load:  ## 把分析结果装载进 MySQL 容器并跑门禁
	python scripts/load_to_mysql.py --backend mysql --host 127.0.0.1 --password $${MYSQL_ROOT_PASSWORD:-employment}

mysql-down:  ## 停掉容器（数据卷保留，下次起来数据还在）
	docker compose down

clean:  ## 清理本地生成物（报告与图表保留）
	rm -rf .pytest_cache .ruff_cache output/employment_analysis.db