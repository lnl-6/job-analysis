"""把 output/report.md 渲染成单文件 HTML（Power BI 的轻量替代品）。

用法：python scripts/render_report_html.py [--md output/report.md] [--out docs/report.html]

为什么不用 Power BI：.pbix 需要 Power BI Desktop 才能生成与刷新；CI、评审者、
Linux 用户都打不开。HTML 版只依赖浏览器，数字与 report.md 完全同源（直接由它转换），
图表引用 output/*.png 的相对路径。Power BI 三页模型仍保留在 docs/powerbi.md 里，
作为「有 BI 环境时怎么做」的指南。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import time
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

CSS = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;max-width:980px;
margin:2rem auto;padding:0 1.2rem;line-height:1.65;color:#1f2328;background:#fff}
h1,h2,h3{line-height:1.3;margin:1.6em 0 .6em}
h1{font-size:1.9em;border-bottom:1px solid #d0d7de;padding-bottom:.3em}
h2{font-size:1.4em;border-bottom:1px solid #eaeef2;padding-bottom:.25em}
table{border-collapse:collapse;margin:1em 0;font-size:.92em;display:block;overflow-x:auto}
th,td{border:1px solid #d0d7de;padding:.4em .7em;text-align:left}
th{background:#f6f8fa}
code{background:#f6f8fa;padding:.1em .35em;border-radius:4px;font-size:.9em}
blockquote{margin:1em 0;padding:.2em 1em;color:#57606a;border-left:4px solid #d0d7de}
img{max-width:100%;border:1px solid #eaeef2;border-radius:6px;margin:.6em 0}
"""


def _inline(text: str) -> str:
    """行内元素：转义 → 粗体 → 行内代码 → 链接 → 图片。"""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="\2">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def render(markdown: str, image_prefix: str = "", provenance: str = "") -> str:
    """把（本仓库报告用到的子集）Markdown 转成 HTML。"""
    lines = markdown.splitlines()
    out: list[str] = []
    table: list[str] = []
    in_code = False

    def flush_table() -> None:
        if not table:
            return
        header = [cell.strip() for cell in table[0].strip("|").split("|")]
        body = [[cell.strip() for cell in row.strip("|").split("|")] for row in table[2:]]
        out.append("<table>")
        out.append("<tr>" + "".join(f"<th>{_inline(cell)}</th>" for cell in header) + "</tr>")
        for row in body:
            out.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>")
        out.append("</table>")
        table.clear()

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            flush_table()
            out.append("<pre><code>" if not in_code else "</code></pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if not line.strip():
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif line.startswith(">"):
            out.append(f"<blockquote>{_inline(line.lstrip('> ').strip())}</blockquote>")
        elif re.match(r"^\s*[-*]\s+", line):
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append("<li>" + _inline(item) + "</li>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    flush_table()

    body = "\n".join(out).replace('src="', f'src="{image_prefix}')
    footer = (
        "<hr><footer><p>本页由 <code>scripts/render_report_html.py</code> 生成，"
        f"数字与 <code>{html.escape(provenance or 'output/report.md')}</code> 同源；"
        "重新运行会变——它不是另一套口径，只是同一份报告的浏览器视图。</p></footer>"
    )
    return f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">" \
           f"<title>招聘岗位数据分析报告</title><style>{CSS}</style></head>" \
           f"<body>{body}{footer}</body></html>"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 report.md 渲染成单文件 HTML")
    parser.add_argument("--md", default=str(ROOT_DIR / "output" / "report.md"))
    parser.add_argument("--out", default=str(ROOT_DIR / "docs" / "report.html"))
    args = parser.parse_args(argv)

    source = Path(args.md)
    if not source.exists():
        print(f"[HTML] 找不到报告：{source}（先跑 python main.py）")
        return 1
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 报告里的图片路径是相对 output/ 的，HTML 放在 docs/ 下，所以要指回 ../output/
    prefix = "../output/" if target.parent.name == "docs" else ""
    markdown = source.read_text(encoding="utf-8")
    # 换行统一成 LF 再算指纹：Windows 工作区是 CRLF、CI 是 LF，不归一化会在 CI 上对不上
    canonical = markdown.replace("\r\n", "\n")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    provenance = f"output/report.md（SHA-256 前 12 位 {digest}，生成于 {stamp}）"
    target.write_text(render(markdown, prefix, provenance), encoding="utf-8", newline="\n")
    print(f"[HTML] 已生成：{target}（图表引用 {prefix or '同目录'}；源指纹 {digest}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())