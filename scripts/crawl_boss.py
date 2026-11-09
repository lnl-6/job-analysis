# -*- coding: utf-8 -*-
"""Boss 直聘岗位采集：低频率、可断点续爬、原始数据落盘。

与参考项目（bosszp / bosszp-selenium / Boss_zhipin_spider）最大的不同，也是这个脚本的三个设计原则：

1. **抓取与解析分离**：本脚本只负责「拿原始 JSON 并逐页落 JSONL」（ODS 层），
   字段解析与口径映射在 `src/sources/boss.py`。好处是抓一次可以反复重放口径，
   口径改了不必重爬。
2. **断点续爬**：已抓过的 (query, city, page) 和已存在的 job id 直接跳过，
   中断后重跑不会重复、也不用从头来。
3. **反爬只做礼敬、不做对抗**：默认延迟 4 秒 + 随机抖动；403 指数退避；
   遇到安全验证（接口返回 code=37）**立即停止并提示**，不尝试绕过验证码。

Cookie 从环境变量 `BOSS_COOKIE` 或 `--cookie-file` 读取，**绝不硬编码、绝不入库**。

⚠️ 合规提示：Boss 直聘用户协议限制自动化抓取。本项目仅用于个人学习、小规模、低频采样；
请自行确认你所在地区与该站点条款的合规性；不要绕过验证码，不要把抓取结果用于商业分发。
本脚本不保存招聘者姓名、联系方式等个人信息（写入前只保留岗位与公司公开字段）。

用法：
    # 第一次：设置 Cookie（登录后在浏览器开发者工具里复制整条 Cookie）
    $env:BOSS_COOKIE = "lastCity=101010100; __zp_stoken__=...."

    # 最小验证：抓 1 页「数据分析」岗位（北京）
    python scripts/crawl_boss.py --query 数据分析 --city 北京 --pages 1

    # 目标规模（推荐起步）：2 个关键词 × 10 个城市 × 5 页
    python scripts/crawl_boss.py --query 数据分析,数据开发 --city 北京,上海,广州,深圳,杭州,成都,武汉,南京,西安,苏州 --pages 5

    # 多轮叠加：一轮跑完等 10 分钟再刷一轮（收集新上架岗位），最多 3 轮、累计 3000 条
    python scripts/crawl_boss.py --query 数据分析,数据开发 --city 北京,上海,广州,深圳,杭州,成都,武汉,南京,西安,苏州 --pages 5 --rounds 3 --round-wait 600 --max-jobs 3000

    # 不联网，只统计本地缓存（CI / 演示用）
    python scripts/crawl_boss.py --offline

抓完再转换口径：
    python -m src.sources.boss --raw data/raw/boss/raw_jobs.jsonl --out "data/raw/boss_岗位数据集.csv"

关于「多轮 / 多城市叠加」：
    - 每轮都是「关键词 × 城市 × 页面」的全组合；同一轮里已抓过的页面会跳过，
      所以 --rounds 3 的语义是「同一批组合再刷 3 遍」，用来收集新上架岗位，不是把页数翻 3 倍。
    - 去重不靠这里：`src/sources/boss.py` 按 job_id 去重，重复抓到的岗位不会进数据集。
    - --max-jobs 是跨轮累计的唯一岗位上限，达到即停。
    - 城市越多、轮数越多，越要控制频率；默认每页 4 秒 + 30% 抖动，单轮 100 个页面约 7 分钟。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src import config  # noqa: E402

# 接口（与网页同一套 wapi；字段名见 src/sources/boss.py）
SEARCH_API = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"

# 城市名 → 区域码（Boss 用 6 位编码；也允许直接传数字编码）
CITY_CODES: dict[str, str] = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "西安": "101110100",
    "苏州": "101190400",
}

#: 只保留这些字段入库：岗位与公司的公开信息，**不含**招聘者姓名/联系方式
KEEP_FIELDS = (
    "encryptJobId",
    "securityId",
    "jobName",
    "salaryDesc",
    "jobDegree",
    "jobExperience",
    "cityName",
    "areaDistrict",
    "businessDistrict",
    "brandName",
    "brandIndustry",
    "brandStageName",
    "brandScaleName",
    "welfareList",
    "skills",
    "jobLabels",
    "jobValidStatus",
)

#: 接口返回码 → 人话
CODE_HINTS = {
    0: "正常",
    37: "触发了安全验证（需要人机验证）",
    403: "被限流/封禁",
}

DEFAULT_OUT = config.DATA_DIR / "boss" / "raw_jobs.jsonl"


def _console_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Boss 直聘岗位采集（低频、可断点续爬；抓取与解析分离）",
    )
    parser.add_argument("--query", default="数据分析", help="搜索关键词，多个用逗号分隔")
    parser.add_argument("--city", default="北京", help="城市名或区域码，多个用逗号分隔")
    parser.add_argument("--pages", type=int, default=5, help="每个关键词 × 城市抓几页（默认 5）")
    parser.add_argument("--page-size", type=int, default=30, help="每页条数（接口上限 30）")
    parser.add_argument("--delay", type=float, default=4.0, help="每页之间的基础间隔秒数（默认 4）")
    parser.add_argument("--jitter", type=float, default=0.3, help="随机抖动比例（默认 0.3）")
    parser.add_argument("--rounds", type=int, default=1, help="刷几轮（默认 1；每轮重扫同一批组合以收集新岗位）")
    parser.add_argument("--round-wait", type=float, default=600.0, help="轮与轮之间的等待秒数（默认 600）")
    parser.add_argument("--max-jobs", type=int, default=3000, help="跨轮累计唯一岗位上限（默认 3000，达到即停）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="原始 JSONL 输出路径")
    parser.add_argument("--cookie-file", default=None, help="Cookie 文本文件（或用环境变量 BOSS_COOKIE）")
    parser.add_argument("--offline", action="store_true", help="不联网，只统计本地缓存")
    return parser.parse_args(argv)


# ---------------------------------------------------------------- 缓存（断点续爬）
def load_cache(path: Path) -> tuple[set[tuple[int, str, str, int]], set[str], list[dict[str, Any]]]:
    """读已有 JSONL：返回 (已抓页面集合, 已有 job id 集合, 原始记录列表)。"""
    pages: set[tuple[int, str, str, int]] = set()
    job_ids: set[str] = set()
    records: list[dict[str, Any]] = []
    if not path.exists():
        return pages, job_ids, records

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            job = payload.get("job") or {}
            job_id = str(job.get("encryptJobId") or "")
            pages.add(
                (
                    int(payload.get("round", 1)),
                    str(payload.get("query", "")),
                    str(payload.get("city_code", "")),
                    int(payload.get("page", 0)),
                )
            )
            if job_id:
                job_ids.add(job_id)
            records.append(payload)
    return pages, job_ids, records


def append_record(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


# ---------------------------------------------------------------- 网络
def _headers(cookie: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.zhipin.com/web/geek/job",
        "Cookie": cookie,
        "X-Requested-With": "XMLHttpRequest",
    }


def fetch_page(query: str, city_code: str, page: int, page_size: int, cookie: str) -> dict[str, Any]:
    """请求一页岗位列表，返回解析后的 JSON（失败时抛 RuntimeError，调用方决定是否退避）。"""
    try:
        import requests  # 局部导入：--offline 模式不需要它
    except ModuleNotFoundError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError("缺少 requests：pip install requests（--offline 模式不需要）") from exc

    params = {"scene": 1, "query": query, "city": city_code, "page": page, "pageSize": page_size}
    response = requests.get(SEARCH_API, params=params, headers=_headers(cookie), timeout=20)

    if response.status_code == 403:
        raise RuntimeError("HTTP 403：被限流或封禁，请降低频率或更换网络")
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("返回的不是 JSON（多半被安全验证拦截，需要浏览器里刷新一次 Cookie）") from exc

    code = payload.get("code")
    if code != 0:
        hint = CODE_HINTS.get(code, "未知错误")
        raise RuntimeError(f"接口返回 code={code}（{hint}）：{payload.get('message') or ''}".strip())
    return payload


def crawl(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    pages_done, seen_ids, records = load_cache(out_path)
    cookies_from_env = os.environ.get("BOSS_COOKIE", "").strip()

    if args.offline:
        queries = {str(item.get("query")) for item in records}
        cities = {str(item.get("city_code")) for item in records}
        rounds = {int(item.get("round", 1)) for item in records}
        print("[offline] 未联网，只统计本地缓存：")
        print(f"          文件：{out_path}（存在：{out_path.exists()}）")
        print(f"          记录：{len(records)} 条｜唯一岗位：{len(seen_ids)} 个｜页面：{len(pages_done)} 个")
        print(f"          轮次：{sorted(rounds) or '无'}｜关键词：{sorted(queries) or '无'}｜城市码：{sorted(cities) or '无'}")
        return 0

    cookie = cookies_from_env
    if args.cookie_file:
        cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    if not cookie:
        print("[错误] 没有 Cookie。请设置环境变量 BOSS_COOKIE，或用 --cookie-file 指定文件。")
        print("       （登录 zhipin.com 后，在开发者工具 Network 里复制任意请求的 Cookie 请求头）")
        return 2

    queries = [item.strip() for item in args.query.split(",") if item.strip()]
    cities = [item.strip() for item in args.city.split(",") if item.strip()]
    total_new_records = 0
    total_new_pages = 0

    for round_index in range(1, args.rounds + 1):
        if args.rounds > 1:
            print(f"\n========== 第 {round_index}/{args.rounds} 轮（累计 {len(seen_ids)} 条唯一岗位）==========")
        round_new_records = 0
        round_new_pages = 0
        stopped = False

        for query in queries:
            for city in cities:
                city_code = CITY_CODES.get(city, city if city.isdigit() else None)
                if not city_code:
                    print(f"[跳过] 不认识的城市：{city}（可传 Boss 的 6 位区域码）")
                    continue

                for page in range(1, args.pages + 1):
                    if (round_index, query, city_code, page) in pages_done:
                        print(f"[跳过] 已抓过：第 {round_index} 轮 {query} @ {city} 第 {page} 页")
                        continue

                    print(f"[抓取] 第 {round_index} 轮 {query} @ {city}({city_code}) 第 {page}/{args.pages} 页 …")
                    try:
                        payload = fetch_page(query, city_code, page, args.page_size, cookie)
                    except RuntimeError as exc:
                        print(f"[停止] {exc}")
                        print("       已抓到的数据都已落盘，调整后重跑即可断点续爬。")
                        return 1

                    job_list = ((payload.get("zpData") or {}).get("jobList")) or []
                    crawled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    for job in job_list:
                        if len(seen_ids) >= args.max_jobs:
                            print(f"[停止] 已达 --max-jobs 上限（{args.max_jobs} 条唯一岗位）")
                            stopped = True
                            break
                        job_id = str(job.get("encryptJobId") or "")
                        if not job_id or job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)
                        append_record(
                            out_path,
                            {
                                "round": round_index,
                                "query": query,
                                "city_code": city_code,
                                "page": page,
                                "crawled_at": crawled_at,
                                "job": {key: job.get(key) for key in KEEP_FIELDS},
                            },
                        )
                        round_new_records += 1
                    pages_done.add((round_index, query, city_code, page))
                    round_new_pages += 1

                    if stopped:
                        break
                    if not ((payload.get("zpData") or {}).get("hasMore")):
                        print(f"[结束] 第 {round_index} 轮 {query} @ {city} 已没有下一页")
                        break

                    time.sleep(args.delay * (1 + random.uniform(0, args.jitter)))

                if stopped:
                    break
            if stopped:
                break

        total_new_records += round_new_records
        total_new_pages += round_new_pages
        print(
            f"[本轮完成] 新增页面 {round_new_pages} 个｜新增岗位 {round_new_records} 条"
            f"｜累计唯一岗位 {len(seen_ids)} 个"
        )

        if stopped or round_index >= args.rounds:
            break
        print(f"[等待] {args.round_wait:.0f} 秒后开始第 {round_index + 1} 轮 …")
        time.sleep(args.round_wait * (1 + random.uniform(0, args.jitter)))

    print("\n[完成] 本次新增：")
    print(f"       页面 {total_new_pages} 个｜岗位 {total_new_records} 条｜累计唯一岗位 {len(seen_ids)} 个")
    print(f"       原始数据：{out_path}")
    print("下一步：python -m src.sources.boss --raw "
          f"\"{out_path}\" --out \"{config.BOSS_DATA_PATH}\"")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    _console_safe()
    args = parse_args(argv)
    if not args.offline and not (os.environ.get("BOSS_COOKIE", "").strip() or args.cookie_file):
        print("[提示] 未设置 Cookie，将只做参数检查并统计缓存。")
        args.offline = True
    return crawl(args)


if __name__ == "__main__":
    raise SystemExit(main())