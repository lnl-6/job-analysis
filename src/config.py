# -*- coding: utf-8 -*-
"""全局配置：路径、列名常量、中文字体设置。

把"魔法字符串"集中在这里，避免散落在各模块里。
"""

from __future__ import annotations

from pathlib import Path

# ---------------- 路径 ----------------
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data" / "raw"
OUTPUT_DIR = ROOT_DIR / "output"
REPORT_DIR = OUTPUT_DIR

# 本仓库的数据集路径（由抓取器生成，不入库）；仓库不带数据文件本身
# Boss 直聘岗位数据集（由 scripts/crawl_boss.py 抓到原始 JSONL，
# 再用 python -m src.sources.boss 转成这个 CSV；口径见 docs/data_source_boss.md）
BOSS_DATA_PATH = DATA_DIR / "boss_岗位数据集.csv"
# 原始抓取缓存（可断点续爬；体量可能很大，已在 .gitignore 里排除）
BOSS_RAW_PATH = DATA_DIR / "boss" / "raw_jobs.jsonl"

# 默认数据源只有一个：本项目的 Boss 抓取产物（BOSS_DATA_PATH）。
# 数据集不入库（见 .gitignore）；clone 后靠现场抓取、或自带样例生成，
# 这样报告里的每个数字都能追溯到「本项目自己采到的数据」。

# ---------------- 列名常量 ----------------
# 说明：本项目**不做性别维度分析**，因此数据里有没有「性别」列都不影响运行。
# 数据列名：整条流水线只认这里（含只进数据库层、不参与结论的列）
COL_ID = "ID"
COL_GENDER = "性别"
COL_EDU = "学历"
COL_MAJOR = "专业"
COL_SALARY = "期望薪资"
COL_SATISFACTION = "满意度评分"
COL_INTERN = "是否有实习经历"
COL_DIRECTION = "毕业去向"
COL_CITY = "工作城市"
COL_INDUSTRY = "就业行业"

# 必需列（不含性别）
REQUIRED_COLUMNS = [
    COL_EDU, COL_MAJOR, COL_SALARY,
    COL_SATISFACTION, COL_INTERN, COL_DIRECTION, COL_CITY,
]

# 主要城市（用于城市对比分析）
MAIN_CITIES = ["上海", "北京", "广州", "深圳", "武汉", "南京"]

# 文本维度里的「占位值」：它们不是有效取值（例如城市列的「无」＝没有城市偏好）。
# 统计会排除它们；数据库层把这类值归到维表的「未指定」成员上，
# 既保证事实表的城市外键非空，又不会在 BI 里冒出一个叫「无」的城市。
UNSPECIFIED_VALUES = ("", "无", "未填", "未指定", "不限", "nan", "None", "-", "/")
CITY_UNSPECIFIED = UNSPECIFIED_VALUES
INDUSTRY_UNSPECIFIED = UNSPECIFIED_VALUES

# 一线城市（用于"一线 vs 新一线"对比）
TIER1_CITIES = ["上海", "北京", "广州", "深圳"]
NEW_TIER1_CITIES = ["武汉", "南京", "杭州", "成都"]

# IQR 异常值判定系数
IQR_FACTOR = 1.5


# ---------------- 数据源声明 ----------------
# 报告溯源区据此打印「这批数字到底是什么口径」——换数据源时最容易犯的错，
# 就是把岗位薪资当成受访者期望薪资继续讲（见 docs/metrics.md 的口径章节）。
# 键是文件名里的匹配片段；值是 (简称, 一句话口径说明)。
SOURCE_NOTES: dict[str, tuple[str, str]] = {
    "boss": (
        "Boss直聘岗位数据",
        "岗位口径：期望薪资=岗位薪资区间中值（元/月）、满意度评分=岗位吸引力代理分（0~5，proxy）、"
        "是否有实习经历=岗位是否面向实习/应届、毕业去向=岗位类型（实习/校招/社招）、专业=岗位方向；"
        "详见 docs/data_source_boss.md。",
    ),
}


def source_note_for(filename: str) -> tuple[str, str] | None:
    """按文件名匹配数据源声明（键长的优先，避免前缀互相覆盖）。"""
    name = str(filename)
    for key in sorted(SOURCE_NOTES, key=len, reverse=True):
        if key in name:
            return SOURCE_NOTES[key]
    return None


# ---------------- 统计与样本量门槛 ----------------
# 所有阈值集中在这里，报告里会如实打印本次实际使用的值，
# 避免出现「阈值写死在代码里、报告却不说」的情况。
MIN_GROUP_SIZE = 30         # 低于此样本量的分组：不给「差异 / 显著」结论
MIN_RECOMMEND_SAMPLES = 5   # 城市参与推荐排序所需的最小样本量
CI_LEVEL = 0.95             # 置信水平
ALPHA = 0.05                # 显著性水平（判定「差异显著」的阈值）

# ---------------- 口径与覆盖率 ----------------
# 方向归类覆盖率低于此值时，报告要显式提示「关键词表跟不上岗位命名」
MAJOR_COVERAGE_WARN = 0.85

# 本列在岗位口径下是吸引力代理分（见 docs/metrics.md 第 8 节）；不是受访者满意度。
# 列名沿用了分析契约的历史叫法，改列名会破坏 7 列契约，所以只在展示层换措辞。
# 表头与结论都要跟着数据源换词，否则就是同一个数字起两个名字。
SATISFACTION_PROXY_TERM = "平均吸引力代理分（proxy）"
DEFAULT_SATISFACTION_TERM = "平均满意度"


def satisfaction_term_for(filename: str) -> str:
    """按数据源返回「满意度」列的中文口径名（岗位数据 → 吸引力代理分）。"""
    return SATISFACTION_PROXY_TERM if "boss" in str(filename).lower() else DEFAULT_SATISFACTION_TERM


def ensure_output_dir() -> Path:
    """确保输出目录存在。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def setup_chinese_font() -> str:
    """为 matplotlib 配置可用的中文字体，返回实际选用的字体名。

    不同操作系统自带的中文字体不同，这里按优先级探测，
    全部找不到时退回默认字体（图上中文可能显示为方块，但不报错）。
    """
    import matplotlib
    from matplotlib import font_manager

    matplotlib.use("Agg")  # 无界面环境也能保存图片

    from matplotlib import pyplot as plt

    candidates = [
        "SimHei",              # Windows
        "Microsoft YaHei",     # Windows
        "PingFang SC",         # macOS
        "Hiragino Sans GB",    # macOS
        "WenQuanYi Micro Hei", # Linux
        "Noto Sans CJK SC",    # Linux
        "Source Han Sans SC",  # Linux
        "Arial Unicode MS",
    ]

    installed = {f.name for f in font_manager.fontManager.ttflist}
    chosen = None
    for name in candidates:
        if name in installed:
            chosen = name
            break

    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen] + plt.rcParams.get("font.sans-serif", [])
        plt.rcParams["axes.unicode_minus"] = False
        return chosen

    plt.rcParams["axes.unicode_minus"] = False
    return "<default>"
