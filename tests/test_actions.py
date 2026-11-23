# -*- coding: utf-8 -*-
"""行动层测试：建议必须由数字机械推出，且可追溯。

这些测试只验证「规则有没有被数据驱动」：
- 每张卡都要有证据与出处，不允许出现没有出处的建议；
- 强度只能在「强/中/弱」里；
- 空 results 必须返回空列表（说明没有硬编码的建议）；
- 报告与 JSON 里必须真的出现这一节。

跑法：`python tests/test_actions.py` 或 `pytest -q tests/test_actions.py`
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np
import pandas as pd

from src import actions, analysis, cleaner, config, report


def _frame(rows: int = 160, seed: int = 21) -> pd.DataFrame:
    """造一批有结构的数据（方向/城市/学历都有差异），保证七张卡都能出。"""
    rng = np.random.default_rng(seed)
    majors = ["数据分析", "数据开发", "数据治理", "数据产品", "算法"]
    cities = ["北京", "上海", "深圳", "广州", "武汉"]
    edus = ["大专", "本科", "硕士", "博士"]
    directions = ["社招", "校招", "实习"]

    major = rng.choice(majors, rows)
    city = rng.choice(cities, rows)
    edu = rng.choice(edus, rows, p=[0.2, 0.55, 0.2, 0.05])
    direction = rng.choice(directions, rows, p=[0.7, 0.2, 0.1])
    base = {"数据分析": 9000, "数据开发": 11000, "数据治理": 9500, "数据产品": 10000, "算法": 13000}
    city_mult = {"北京": 1.15, "上海": 1.12, "深圳": 1.10, "广州": 1.0, "武汉": 0.92}
    edu_mult = {"大专": 0.85, "本科": 1.0, "硕士": 1.12, "博士": 1.2}
    salary = np.array(
        [
            base[m] * city_mult[c] * edu_mult[e] * rng.uniform(0.9, 1.1)
            for m, c, e in zip(major, city, edu)
        ]
    )
    return pd.DataFrame(
        {
            config.COL_MAJOR: major,
            config.COL_CITY: city,
            config.COL_EDU: edu,
            config.COL_DIRECTION: direction,
            config.COL_INTERN: rng.choice(["是", "否"], rows, p=[0.2, 0.8]),
            config.COL_SALARY: np.round(salary, -2),
            config.COL_SATISFACTION: np.round(rng.uniform(2.0, 4.5, rows), 1),
            config.COL_GENDER: rng.choice(["男", "女"], rows),
            "job_type": direction,
        }
    )


def _results() -> dict[str, object]:
    frame = _frame()
    cleaned, _ = cleaner.clean(frame)
    return analysis.run_all(cleaned, top_n=5)


def test_build_actions_returns_cards() -> None:
    cards = actions.build_actions(_results(), meta=None)
    assert cards, "有数据时必须至少产出一张行动卡"
    keys = [card.key for card in cards]
    assert len(keys) == len(set(keys)), "卡片 key 不能重复"


def test_every_card_has_evidence_and_source() -> None:
    for card in actions.build_actions(_results()):
        assert card.evidence, f"{card.key} 没有证据"
        assert card.source, f"{card.key} 没有出处"
        assert card.finding and card.action


def test_strength_in_allowed_set() -> None:
    for card in actions.build_actions(_results()):
        assert card.strength in actions.ALLOWED_STRENGTHS


def test_empty_results_returns_empty_cards() -> None:
    """空 results 返回空列表 —— 说明没有任何硬编码建议。"""
    assert actions.build_actions({}) == []
    assert actions.to_frame([]).empty


def test_actions_frame_columns() -> None:
    frame = actions.to_frame(actions.build_actions(_results()))
    assert list(frame.columns) == ["卡片", "发现", "建议", "强度", "证据", "出处"]
    assert len(frame) >= 5


def test_actions_json_serializable() -> None:
    payload = actions.to_json(actions.build_actions(_results()))
    text = json.dumps(payload, ensure_ascii=False)
    assert "key" in text and "credibility" in text


def test_report_and_json_contain_actions() -> None:
    """报告里要有第六节，且 output/report.json 能生成。"""
    results = _results()
    frame = _frame()
    cleaned, cleaning = cleaner.clean(frame)
    info = report.DataSourceInfo(path=Path("boss_岗位数据集.csv"), rows=len(cleaned), cols=cleaned.shape[1])

    original = config.OUTPUT_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config.OUTPUT_DIR = Path(tmp)  # type: ignore[misc]
            report.write_markdown_report(info, cleaning, results, [], {})
            text = (Path(tmp) / "report.md").read_text(encoding="utf-8")
            assert "## 六、行动建议（描述性，不是预测）" in text
            assert "## 九、方法与局限" in text

            json_path = report.write_json_report(info, results)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            assert payload["actions"] and payload["data"]["file"].endswith(".csv")
    finally:
        config.OUTPUT_DIR = original  # type: ignore[misc]


def main() -> int:
    tests = [
        ("行动卡能生成且 key 不重复", test_build_actions_returns_cards),
        ("每张卡都有证据与出处", test_every_card_has_evidence_and_source),
        ("强度只在强/中/弱里", test_strength_in_allowed_set),
        ("空 results 返回空卡片", test_empty_results_returns_empty_cards),
        ("卡片表格列固定", test_actions_frame_columns),
        ("卡片可序列化为 JSON", test_actions_json_serializable),
        ("报告第六节 + report.json 存在", test_report_and_json_contain_actions),
    ]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"[OK  ] {name}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {name} —— {exc}")
        except Exception as exc:  # noqa: BLE001 - 测试脚本要打印出具体异常
            failed += 1
            print(f"[ERR ] {name} —— {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())