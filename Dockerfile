# 运行环境就用 analysis 依赖，不带测试与抓取依赖（抓取在宿主机跑，容器只负责分析）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# 先装依赖再拷代码：改代码不会让依赖层失效
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据目录在运行时挂载（镜像里不放任何抓取数据）
VOLUME ["/app/data/raw", "/app/output"]

ENTRYPOINT ["python", "main.py"]
CMD ["--top", "8"]