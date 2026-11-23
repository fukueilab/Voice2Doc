"""画面プレビューの表（app.js の markdownTable / renderMarkdown）のテスト（Node で実行）"""
import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

import pytest

from src_backends.output_formats import Table, parse_markdown

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "src" / "style.css").read_text(encoding="utf-8")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")


def _table_source() -> str:
    m = re.search(r"// <markdownTable>\n(.*?)// </markdownTable>", APP_JS, re.S)
    assert m, "markdownTable の目印コメントが見つからない"
    return m.group(1)


def _render_source() -> str:
    start = APP_JS.index("  function renderMarkdown(")
    return APP_JS[start:APP_JS.index("  function showResults(")]


def _node(expression: str):
    script = _table_source() + _render_source() + f"\nconsole.log(JSON.stringify({expression}));\n"
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, encoding="utf-8",
                         check=True, timeout=30)
    return json.loads(out.stdout)


def _table(md: str, start: int = 0):
    return _node(f"markdownTable({json.dumps(md)}.split('\\n'), {start})")


def _render(md: str) -> str:
    return _node(f"renderMarkdown({json.dumps(md)})")


def _grids(html: str):
    """HTMLの表を、文字列の二次元配列にする"""
    grids = []
    for table in re.findall(r"<table>(.*?)</table>", html, re.S):
        rows = re.findall(r"<tr>(.*?)</tr>", table, re.S)
        grids.append([[unescape(re.sub(r"<[^>]+>", "", c.replace("<br>", "\n")))
                       for c in re.findall(r"<t[hd](?: [^>]*)?>(.*?)</t[hd]>", r, re.S)] for r in rows])
    return grids


@needs_node
class TestMarkdownTable:
    def test_html_and_next_position(self):
        r = _table("前\n| a | b |\n|:-:|--:|\n| **1** | *2* |\n後", 1)
        assert r["next"] == 4
        assert r["html"] == (
            '<div class="md-table"><table><thead><tr><th class="md-center">a</th>'
            '<th class="md-right">b</th></tr></thead>'
            '<tbody><tr><td class="md-center"><strong>1</strong></td><td class="md-right"><em>2</em></td></tr></tbody>'
            "</table></div>")

    def test_not_a_table_is_null(self):
        assert _table("| a | b |\n| 1 | 2 |") is None
        assert _table("本文") is None
        assert _table("| a | b |") is None

    def test_cells_are_escaped(self):
        html = _render("| <script>alert(1)</script> | a&b |\n|---|---|\n| <img src=x onerror=alert(1)> | \"q\" 'r' |")
        assert "<script" not in html and "<img" not in html
        assert "&lt;script&gt;" in html and "a&amp;b" in html and "&quot;q&quot;" in html

    def test_br_is_restored_but_attributes_are_not(self):
        html = _render("| a | b |\n|---|---|\n| 1<br>2<BR/>3 | <br onclick=x> |")
        assert "1<br>2<br>3" in html and "&lt;br onclick=x&gt;" in html

    def test_header_only_has_no_body_rows(self):
        assert _grids(_render("| a | b |\n|---|---|")) == [[["a", "b"]]]

    def test_no_newline_in_html(self):
        assert "\n" not in _render("| a | b |\n|---|---|\n| 1 | 2 |\n\n本文")


# 変換（parse_markdown）と画面で、表とみなす範囲・セルの分け方が同じこと
CASES = [
    "| a | b |\n|---|---|\n| 1 | 2 |",
    "a | b\n---|---\n1 | 2",
    "|a|b|\n| - | :-: |\n|1|2|",
    "| a \\| b | c |\n|---|---|\n| 1 | x\\|y |",
    "| a | b |\n|---|:-:|\n| 1 |\n| 1 | 2 | 3 |",
    "| | b |\n|---|---|\n| 1 | |\n||2|",
    "| a | b |\n|---|---|\n| - | - |",
    "|a|b|\n|---|---|\n|1|2|\n|---|---|\n|3|4|",
    "- 項目\n  | a | b |\n  |---|---|\n  | 1 | 2 |\n- 次",
    "前文\n| a | b |\n|---|---|\n| 1 | 2 |\n後文",
    "|a|b|\n|-|-|\n|1|2|\n\n|c|d|\n|-|-|\n|3|4|",
    "|a|b|\n|-|-|\n|1|2|\n# 見出し | x",
    "|a|b|\n|-|-|\n|1|2|\n- 項目 | x",
    "|a|b|\n|-|-|\n|1|2|\n1. 番号 | x",
    "|a|b|\n|-|-|\n|1|2|\n> 引用 | x",
    "|a|b|\n|-|-|\n|1|2|\n```\n|x|y|\n|-|-|\n```\n|c|d|\n|-|-|",
    "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```",
    "~~~\n| a | b |\n|---|---|\n~~~",
    "# a | b\n---|---",
    "- a | b\n---|---",
    "> a | b\n---|---",
    "a | b\n---",
    "|---|---|\n| a | b |\n|---|---|\n| 1 | 2 |",
    "| a | b |\n| 1 | 2 |",
    "| a |\n|---|\n| 1<br>2 |",
    "| **太**と*斜* | 2 * 3 * 4 |\n|---|---|\n| ***両*** | a * b |",
    "a | b\\|\n---|---\n1 | 2\\|",
    "|a|b|\n|---|---|\n|1|2|\n|--|--|\n|3|4|",
    "１. 項目 | 内容\n---|---\nあ | い",
    "\ufeff| a | b |\n|---|---|\n| 1 | 2 |",
]


@needs_node
@pytest.mark.parametrize("md", CASES)
def test_same_rules_as_the_converter(md):
    expected = [[["".join(r.text for r in c) for c in row] for row in b.grid()]
                for b in parse_markdown(md) if isinstance(b, Table)]
    assert _grids(_render(md)) == expected


@needs_node
class TestRenderMarkdownUnchanged:
    """表以外の表示は変えない"""

    def test_headings_lists_and_text(self):
        assert _render("# A\n## B\n### C\n- x\n- **y**\n本文\n\n*斜*") == (
            "<h1>A</h1><h2>B</h2><h3>C</h3><ul><li>x</li><li><strong>y</strong></li></ul>本文<br><br><em>斜</em><br>")

    def test_list_is_closed_before_the_table(self):
        html = _render("- x\n| a |\n|---|\n| 1 |\n- y")
        assert html.startswith('<ul><li>x</li></ul><div class="md-table">')
        assert html.endswith("</div><ul><li>y</li>") or html.endswith("</div><ul><li>y</li></ul>")


def test_style_has_table_rules_for_both_themes():
    block = STYLE_CSS[STYLE_CSS.index(".markdown-body .md-table"):]
    block = block[:block.index("/* 設定画面 */")]
    assert "overflow-x: auto" in block and "var(--text-light)" in block and "var(--bg-tertiary)" in block
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "色は変数で指定する（ライト/ダーク両対応）"
