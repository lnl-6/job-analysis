"""报告快照与 HTML 视图的一致性测试（6.3 / 6.4 收尾）。

约定：
- `docs/sample_report.md` 是一次真实运行的报告快照，必须带溯源标记，且不能含「预测」类结论；
- `docs/report.html` 由 `scripts/render_report_html.py` 生成，必须写明来源指纹；
- 两者都与 `output/` 无关地存在（CI 里没有 output 时跳过内容比对，但文件本身必须在）。
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from scripts import render_report_html  # noqa: E402

SNAPSHOT = ROOT_DIR / "docs" / "sample_report.md"
HTML_DOC = ROOT_DIR / "docs" / "report.html"
LIVE_REPORT = ROOT_DIR / "output" / "report.md"


def test_snapshot_has_provenance() -> None:
    """快照必须能回答「这是哪次运行的结果」。"""
    text = SNAPSHOT.read_text(encoding="utf-8")
    assert "数据指纹" in text
    assert "本报告由 `main.py` 自动生成" in text
    assert "重新运行" in text or "会变" in text


def test_snapshot_matches_live_report_when_present() -> None:
    """有 output/report.md 时，快照的核心段落应与之一致（允许快照有新增头注）。"""
    if not LIVE_REPORT.exists():
        return
    live = LIVE_REPORT.read_text(encoding="utf-8")
    snap = SNAPSHOT.read_text(encoding="utf-8")
    # 快照里把绝对路径改成了相对路径，比对时统一口径
    live = live.replace(str(ROOT_DIR) + "\\", "").replace(str(ROOT_DIR) + "/", "")
    live_body = live.split("## 一、", 1)[-1]
    snap_body = snap.split("## 一、", 1)[-1]
    assert live_body.strip() == snap_body.strip(), "快照落后于 output/report.md，请重新生成快照"


def test_html_records_source_fingerprint() -> None:
    """HTML 视图必须写明来自哪份 report.md（指纹），否则无法判断是否过期。"""
    text = HTML_DOC.read_text(encoding="utf-8")
    assert "scripts/render_report_html.py" in text
    assert "SHA-256 前 12 位" in text
    source = re.search(r"output/report\.md（SHA-256 前 12 位 ([0-9a-f]{12})", text)
    assert source is not None
    if LIVE_REPORT.exists():
        digest = hashlib.sha256(LIVE_REPORT.read_text(encoding="utf-8").encode("utf-8")).hexdigest()[:12]
        assert source.group(1) == digest, "docs/report.html 过期，请重跑 scripts/render_report_html.py"


def test_render_includes_images_and_footer() -> None:
    """渲染器本身：图片前缀、页脚、表格都不能丢。"""
    markdown = "# 标题\n\n![图](01_x.png)\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    page = render_report_html.render(markdown, "../output/", "output/report.md（测试）")
    assert 'src="../output/01_x.png"' in page
    assert "<table>" in page and "<td>1</td>" in page
    assert "本页由" in page and "output/report.md（测试）" in page


def main() -> int:
    checks = [
        ("快照带溯源标记", test_snapshot_has_provenance),
        ("快照与最新报告一致", test_snapshot_matches_live_report_when_present),
        ("HTML 记录来源指纹", test_html_records_source_fingerprint),
        ("渲染器保留图片/页脚/表格", test_render_includes_images_and_footer),
    ]
    failed = 0
    for name, func in checks:
        try:
            func()
            print(f"[OK] {name}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
    print(f"\n{len(checks) - failed}/{len(checks)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())