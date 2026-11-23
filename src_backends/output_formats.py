"""履歴のMarkdownを txt / docx / PDF へ変換する出力形式モジュール。

Markdownを一度、見出し・段落・リスト・表・太字からなる中間データ（IR）に解析し、
そこから各形式のファイルを生成する。Markdown記号は利用者に見せない。
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union


@dataclass(frozen=True)
class Run:
    """インライン要素（同じ装飾が続く文字列）"""
    text: str
    bold: bool = False


@dataclass
class Heading:
    level: int
    runs: List[Run] = field(default_factory=list)


@dataclass
class Paragraph:
    runs: List[Run] = field(default_factory=list)


@dataclass
class ListItem:
    depth: int
    ordered: bool
    number: int
    runs: List[Run] = field(default_factory=list)


@dataclass
class Blank:
    """段落区切りの空行（連続する空行は1つに畳む）"""


Cell = List[Run]


@dataclass
class Table:
    """表。セルの文字列に含まれる "\\n" はセル内改行（<br>）を表す。

    rows は Markdown に書かれたままのセル数を保つ（txt で余分な区切りを出さないため）。
    列数を揃えた表が要るときは grid() を使う。"""
    header: List[Cell] = field(default_factory=list)
    rows: List[List[Cell]] = field(default_factory=list)
    aligns: List[str] = field(default_factory=list)  # "left" / "center" / "right"（列数分）

    @property
    def ncols(self) -> int:
        return max(len(row) for row in [self.header, *self.rows])

    def grid(self) -> List[List[Cell]]:
        """見出し行を先頭に、全行の列数を揃えた二次元配列（足りないセルは空）"""
        n = self.ncols
        return [list(row) + [[] for _ in range(n - len(row))] for row in [self.header, *self.rows]]


Block = Union[Heading, Paragraph, ListItem, Table, Blank]

# 末尾の閉じ「#」は直前に空白がある場合だけ除去する（「# C#」の「#」は本文として残す）
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+・]\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_QUOTE_RE = re.compile(r"^\s*(?:>\s?)+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
# 表のセルの区切り（「\|」は区切りではなく文字の | ）と、区切り行のセル（例: ---、:-:）
_PIPE_RE = re.compile(r"(?<!\\)\|")
_TABLE_SEP_CELL_RE = re.compile(r":?-+:?")
_TABLE_LONG_SEP_CELL_RE = re.compile(r":?-{2,}:?")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"\*(?!\s)([^*]+?)(?<!\s)\*")
_CODE_RE = re.compile(r"`([^`]*)`")
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
# バックスラッシュエスケープ（\* \_）は解析中だけ私用領域の文字に退避し、最後に元の記号へ戻す
_ESCAPES = {"\\*": "", "\\_": ""}
_UNESCAPES = {"": "*", "": "_"}
# XML 1.0で使えない制御文字（docx生成が失敗するため解析の入口で除去する）
_XML_INVALID_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _link_to_text(m: "re.Match") -> str:
    return m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})"


def _strip_markup(text: str) -> str:
    """太字以外のインライン記法（斜体・未閉じの強調記号）を除去する"""
    return _ITALIC_RE.sub(r"\1", text).replace("**", "")


def parse_inline(text: str) -> List[Run]:
    """1行分のインライン記法を解析してRun列にする（太字のみ装飾として保持）"""
    for src, placeholder in _ESCAPES.items():
        text = text.replace(src, placeholder)
    text = _LINK_RE.sub(_link_to_text, text)
    text = _CODE_RE.sub(r"\1", text)
    text = _BOLD_ITALIC_RE.sub(r"**\1**", text)
    runs: List[Run] = []
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            runs.append(Run(_strip_markup(text[pos:m.start()])))
        runs.append(Run(_strip_markup(m.group(1) or m.group(2)), bold=True))
        pos = m.end()
    if pos < len(text):
        runs.append(Run(_strip_markup(text[pos:])))
    for placeholder, char in _UNESCAPES.items():
        runs = [Run(r.text.replace(placeholder, char), r.bold) for r in runs]
    return [r for r in runs if r.text]


def _plain_run(text: str) -> List[Run]:
    return [Run(text)] if text else []


def _table_row(line: str) -> str:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return " | ".join(c for c in cells)


# ---- 表の判定（src/app.js の markdownTable と同じ規則にする） ----

def _split_cells(line: str) -> List[str]:
    """表の1行をセルの文字列に分ける（前後の | は有っても無くてもよい）"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in _PIPE_RE.split(s)]


def _is_table_row(line: str) -> bool:
    """表の行になりうるか（| を含み、見出し・リスト・引用・コードフェンスではない）"""
    return bool(_PIPE_RE.search(line)) and not any(
        r.match(line) for r in (_FENCE_RE, _HEADING_RE, _BULLET_RE, _ORDERED_RE, _QUOTE_RE))


def _is_table_separator(line: str) -> bool:
    return bool(_PIPE_RE.search(line)) and all(_TABLE_SEP_CELL_RE.fullmatch(c) for c in _split_cells(line))


def _parse_cell(text: str) -> Cell:
    return [Run(_BR_RE.sub("\n", r.text), r.bold) for r in parse_inline(text)]


def _align_of(cell: str) -> str:
    if cell.endswith(":"):
        return "center" if cell.startswith(":") else "right"
    return "left"


def _parse_table(lines: List[str], start: int) -> Optional[Tuple[Table, int]]:
    """lines[start] から始まる表を解析し、(表, 表の次の行の位置) を返す。表でなければ None"""
    if start + 1 >= len(lines):
        return None
    head, sep = lines[start].rstrip(), lines[start + 1].rstrip()
    if not _is_table_row(head) or _is_table_separator(head) or not _is_table_separator(sep):
        return None
    table = Table(header=[_parse_cell(c) for c in _split_cells(head)],
                  aligns=[_align_of(c) for c in _split_cells(sep)])
    i = start + 2
    while i < len(lines) and _is_table_row(lines[i].rstrip()):
        cells = _split_cells(lines[i].rstrip())
        # 本文に繰り返された区切り行は捨てる。「-」だけのセルは「該当なし」の意味で使われるため残す
        if not all(_TABLE_LONG_SEP_CELL_RE.fullmatch(c) for c in cells):
            table.rows.append([_parse_cell(c) for c in cells])
        i += 1
    n = table.ncols
    table.aligns = (table.aligns + ["left"] * n)[:n]
    return table, i


def parse_markdown(text: str) -> List[Block]:
    """MarkdownをIR（Blockのリスト）に解析する"""
    blocks: List[Block] = []
    if not text:
        return blocks
    text = _XML_INVALID_RE.sub("", text.lstrip("\ufeff"))

    def add_blank():
        if blocks and not isinstance(blocks[-1], Blank):
            blocks.append(Blank())

    in_fence = False
    indent_stack: List[int] = []  # リスト入れ子の深さ判定用（インデント幅の昇順）

    def list_depth(indent: int) -> int:
        while indent_stack and indent_stack[-1] > indent:
            indent_stack.pop()
        if not indent_stack or indent_stack[-1] < indent:
            indent_stack.append(indent)
        return len(indent_stack) - 1

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    pos = 0
    while pos < len(lines):
        line = lines[pos].rstrip()
        pos += 1

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            indent_stack.clear()
            continue
        if in_fence:
            if line:
                blocks.append(Paragraph(_plain_run(line.strip("\t"))))
            else:
                add_blank()
            continue

        if not line.strip():
            add_blank()
            continue

        if _HR_RE.match(line):
            indent_stack.clear()
            add_blank()
            continue

        m = _HEADING_RE.match(line)
        if m:
            indent_stack.clear()
            blocks.append(Heading(len(m.group(1)), parse_inline(m.group(2))))
            continue

        m = _BULLET_RE.match(line)
        if m:
            depth = list_depth(len(m.group(1).expandtabs(4)))
            blocks.append(ListItem(depth, False, 0, parse_inline(m.group(2))))
            continue

        m = _ORDERED_RE.match(line)
        if m:
            depth = list_depth(len(m.group(1).expandtabs(4)))
            blocks.append(ListItem(depth, True, int(m.group(2)), parse_inline(m.group(3))))
            continue

        indent_stack.clear()

        m = _QUOTE_RE.match(line)
        if m:
            blocks.append(Paragraph(parse_inline(m.group(1).strip())))
            continue

        parsed = _parse_table(lines, pos - 1)
        if parsed:
            table, pos = parsed
            blocks.append(table)
            continue

        # 区切り行の無い「|」の行は表とみなさず、記号を除いた段落にする
        if line.lstrip().startswith("|"):
            if not _TABLE_SEP_RE.match(line):
                blocks.append(Paragraph(parse_inline(_table_row(line))))
            continue

        blocks.append(Paragraph(parse_inline(line.strip())))

    # 先頭・末尾の空行は不要
    while blocks and isinstance(blocks[-1], Blank):
        blocks.pop()
    while blocks and isinstance(blocks[0], Blank):
        blocks.pop(0)
    return blocks


# ==================== txt ====================

def _runs_text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def blocks_to_txt(blocks: List[Block]) -> bytes:
    """IRをプレーンテキスト(UTF-8 BOM付き・CRLF)にする。

    見出しは前後に空行を入れ、箇条書きは「・」、入れ子は全角スペースでインデントする。
    BOMはWindowsのメモ帳等での文字化けを避けるために付ける。"""
    lines: List[str] = []

    def blank():
        if lines and lines[-1] != "":
            lines.append("")

    for b in blocks:
        if isinstance(b, Heading):
            blank()
            lines.append(_runs_text(b.runs))
            lines.append("")
        elif isinstance(b, ListItem):
            marker = f"{b.number}. " if b.ordered else "・"
            lines.append("　" * b.depth + marker + _runs_text(b.runs))
        elif isinstance(b, Paragraph):
            lines.append(_runs_text(b.runs))
        elif isinstance(b, Table):
            for row in [b.header, *b.rows]:
                lines.append(" | ".join(_runs_text(c).replace("\n", " ") for c in row))
        else:
            blank()
    while lines and lines[-1] == "":
        lines.pop()
    return ("\r\n".join(lines) + "\r\n").encode("utf-8-sig")


def markdown_to_txt(markdown: str) -> bytes:
    return blocks_to_txt(parse_markdown(markdown))


# ==================== 表の列幅（docx・PDF共通） ====================

_PT_MM = 25.4 / 72
TABLE_MIN_PT = 5.0
_COL_WEIGHT_MAX = 60   # 1つの長いセルが幅を独占しないための上限（半角換算の文字数）
_COL_WEIGHT_PAD = 2
_COL_WORD_MAX = 12     # 途中で折り返さないように幅を確保する語の長さの上限（半角換算。日付など）
# 途中で改行しない文字の並び（空白と日本語の文字以外）
_WORD_RE = re.compile(r"[^\s\u3000-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]+")


def display_width(text: str) -> int:
    """半角=1・全角=2 で数えた幅（複数行なら最も長い行）"""
    return max(sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in line)
               for line in text.split("\n"))


def column_widths(table: Table, total: float, min_width: float, char_width: float = 0.0) -> List[float]:
    """セルの文字量に比例した列幅を返す（合計は total。1列の幅は min_width 以上）。

    char_width（半角1文字の幅）を渡すと、短いセルや日付などの短い英数字が途中で折り返さない幅も確保する。
    確保しきれないほど列が多いときは均等に分ける。"""
    n = table.ncols
    if n * min_width >= total:
        return [total / n] * n
    columns = [[_runs_text(c) for c in col] for col in zip(*table.grid())]
    weights = [min(max(display_width(t) for t in col), _COL_WEIGHT_MAX) + _COL_WEIGHT_PAD for col in columns]
    floors = [min_width] * n
    if char_width:
        def reserve(with_cells: bool) -> List[float]:
            result = []
            for col in columns:
                lengths = [len(w) for t in col for w in _WORD_RE.findall(t)]
                if with_cells:
                    lengths += [display_width(line) for t in col for line in t.split("\n")]
                longest = max((k for k in lengths if k <= _COL_WORD_MAX), default=0)
                result.append(max(min_width, (longest + _COL_WEIGHT_PAD) * char_width))
            return result

        for wide in (reserve(True), reserve(False)):
            if sum(wide) < total:
                floors = wide
                break
    widths = [0.0] * n
    free = set(range(n))
    while True:
        rest = total - sum(floors[i] for i in range(n) if i not in free)
        weight_sum = sum(weights[i] for i in free)
        for i in range(n):
            widths[i] = rest * weights[i] / weight_sum if i in free else floors[i]
        short = {i for i in free if widths[i] < floors[i]}
        if not short:
            return widths
        free -= short


def table_font_pt(col_mm: float, pad_mm: float, base_pt: float) -> float:
    """列が多いとき、1列に全角4文字程度入るまで小さくした文字の大きさ（下限 TABLE_MIN_PT）"""
    return max(TABLE_MIN_PT, min(base_pt, (col_mm - 2 * pad_mm) / 4 / _PT_MM))


# ==================== docx ====================

DOCX_FONT = "游ゴシック"
_DOCX_HEADING_MAX = 3
_DOCX_LIST_MAX_DEPTH = 3
# Markdownの # と ## に対応する見出し(Word側のレベル)。ボールドにする
_DOCX_BOLD_HEADING_MAX = 2
_DOCX_TABLE_STYLE = "Table Grid"
_DOCX_TABLE_MIN_COL_MM = 12
_DOCX_BODY_PT = 11.0
_DOCX_HALF_CHAR_EM = 0.6   # 英数字1文字のおおよその幅（文字の大きさに対する比）
_DOCX_CELL_PAD_MM = 1.9    # Wordの既定のセル左右余白
_DOCX_KEEP_ROW_LINES = 15  # これより低い行はページの途中で分けない
_DOCX_MAX_COLUMNS = 63     # Wordの表の列数の上限
TABLE_HEADER_FILL = "F2F2F2"


def _set_docx_font(style, name: str) -> None:
    """スタイルのフォントを（日本語=eastAsia含めて）固定する。テーマフォント指定は上書きされるため除去する"""
    style.font.name = name
    rfonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        rfonts.attrib.pop(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{attr}", None)
    rfonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", name)


def _limit_columns(table: Table, limit: int) -> Table:
    """列数が上限を超える表の、超えた分のセルを最後の列へまとめる（文字は捨てない）"""
    if table.ncols <= limit:
        return table

    def merge(row: List[Cell]) -> List[Cell]:
        last: Cell = []
        for i, cell in enumerate(row[limit - 1:]):
            last += ([Run(" | ")] if i else []) + cell
        return row[:limit - 1] + [last]

    grid = [merge(row) for row in table.grid()]
    return Table(grid[0], grid[1:], table.aligns[:limit])


def _add_docx_table(doc, table: Table, add_runs) -> None:
    """表を追加する。全セルに罫線、見出し行は太字・灰色背景にし、ページをまたぐと繰り返す"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Emu, Mm, Pt

    table = _limit_columns(table, _DOCX_MAX_COLUMNS)
    grid = table.grid()
    section = doc.sections[0]
    total = section.page_width - section.left_margin - section.right_margin
    col_mm = Emu(total).mm / table.ncols
    # 列が多いときは文字とセルの余白を小さくする
    pad_mm = min(_DOCX_CELL_PAD_MM, col_mm * 0.15)
    size = table_font_pt(col_mm, pad_mm, _DOCX_BODY_PT)
    char_mm = size * _PT_MM * _DOCX_HALF_CHAR_EM
    widths = [Emu(int(w)) for w in column_widths(table, total, Mm(_DOCX_TABLE_MIN_COL_MM), Mm(char_mm))]
    aligns = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT}

    t = doc.add_table(rows=len(grid), cols=table.ncols, style=_DOCX_TABLE_STYLE)
    t.autofit = False
    if pad_mm < _DOCX_CELL_PAD_MM:
        margins = OxmlElement("w:tblCellMar")
        for side in ("left", "right"):
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:w"), str(int(Mm(pad_mm).twips)))
            el.set(qn("w:type"), "dxa")
            margins.append(el)
        # tblCellMar は tblLayout の後・tblLook の前に置く
        t._tbl.tblPr.find(qn("w:tblLayout")).addnext(margins)

    def line_count(cell: Cell, width) -> float:
        per_line = max(1.0, (Emu(width).mm - 2 * pad_mm) / char_mm)
        return sum(1 + display_width(part) / per_line for part in _runs_text(cell).split("\n"))

    for column, width in zip(t.columns, widths):
        column.width = width
    for r, (row, cells) in enumerate(zip(t.rows, grid)):
        for c, (cell, runs) in enumerate(zip(row.cells, cells)):
            cell.width = widths[c]
            paragraph = cell.paragraphs[0]
            if table.aligns[c] in aligns:
                paragraph.alignment = aligns[table.aligns[c]]
            add_runs(paragraph, runs, force_bold=r == 0)
            if size < _DOCX_BODY_PT:
                for run in paragraph.runs:
                    run.font.size = Pt(size)
            if r == 0:
                # 見出し行だけがページの末尾に残らないようにする
                paragraph.paragraph_format.keep_with_next = True
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:color"), "auto")
                shd.set(qn("w:fill"), TABLE_HEADER_FILL)
                cell._tc.get_or_add_tcPr().append(shd)
        if max(line_count(cell, w) for cell, w in zip(cells, widths)) <= _DOCX_KEEP_ROW_LINES:
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        if r == 0:
            row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))


def blocks_to_docx(blocks: List[Block]) -> bytes:
    """IRをWord(.docx)にする。見出し→見出し1〜3、箇条書き→箇条書きスタイル、表→罫線付きの表、太字→太字。

    番号付きリストはWordの自動採番だと別々のリストの番号が連続してしまうため、
    Markdown上の番号をそのまま本文に出力し、ぶら下げインデントで体裁を整える。"""
    import io

    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    for style_name in ("Normal", "Heading 1", "Heading 2", "Heading 3",
                       "List Bullet", "List Bullet 2", "List Bullet 3"):
        _set_docx_font(doc.styles[style_name], DOCX_FONT)
    # 文字色は全て黒にする。既定テンプレートの見出しスタイルは青系のテーマ色(themeColor付き)だが、
    # python-docxの color.rgb セッターは w:color を作り直して themeColor を除去するため黒で固定される
    for style_name in ("Normal", "Heading 1", "Heading 2", "Heading 3",
                       "List Bullet", "List Bullet 2", "List Bullet 3", "List Paragraph"):
        doc.styles[style_name].font.color.rgb = RGBColor(0, 0, 0)
    # H1・H2 は（テンプレート既定に頼らず）スタイルでもボールドを明示する
    for level in range(1, _DOCX_BOLD_HEADING_MAX + 1):
        doc.styles[f"Heading {level}"].font.bold = True

    def add_runs(paragraph, runs: List[Run], force_bold: bool = False) -> None:
        for r in runs:
            run = paragraph.add_run(r.text)
            if r.bold or force_bold:
                run.bold = True

    after_table = False
    for b in blocks:
        if isinstance(b, Blank):
            continue  # Wordは段落間隔で区切られるため出力しない
        # 表が続くとWordが1つの表に結合するため、間に空の段落を挟む
        if isinstance(b, Table) and after_table:
            doc.add_paragraph()
        after_table = isinstance(b, Table)
        if isinstance(b, Heading):
            level = min(b.level, _DOCX_HEADING_MAX)
            add_runs(doc.add_paragraph(style=f"Heading {level}"), b.runs,
                     force_bold=b.level <= _DOCX_BOLD_HEADING_MAX)
        elif isinstance(b, ListItem) and not b.ordered:
            depth = min(b.depth, _DOCX_LIST_MAX_DEPTH - 1)
            style = "List Bullet" if depth == 0 else f"List Bullet {depth + 1}"
            add_runs(doc.add_paragraph(style=style), b.runs)
        elif isinstance(b, ListItem):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18 * (b.depth + 1))
            p.paragraph_format.first_line_indent = Pt(-18)
            p.add_run(f"{b.number}. ")
            add_runs(p, b.runs)
        elif isinstance(b, Paragraph):
            add_runs(doc.add_paragraph(), b.runs)
        elif isinstance(b, Table):
            _add_docx_table(doc, b, add_runs)
    if after_table:
        doc.add_paragraph()  # 文書の最後は段落でなければならない

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def markdown_to_docx(markdown: str) -> bytes:
    return blocks_to_docx(parse_markdown(markdown))


# ==================== PDF ====================

PDF_FONT_FAMILY = "IPAexGothic"
PDF_FONT_FILENAME = "ipaexg.ttf"
_PDF_BODY_PT = 10.5
_PDF_HEADING_PT = {1: 18, 2: 15, 3: 12.5}
_PDF_LINE_MM = 6.5
_PDF_INDENT_MM = 6.0
_PDF_MAX_LIST_DEPTH = 6
_CJK_RE = re.compile(r"[　-ヿ㐀-鿿豈-﫿＀-￯]")
# 描画上書きで太字に見せる際の輪郭線幅(mm)。本文は細め、見出しはやや太め
_PDF_FAKE_BOLD_MM = {"body": 0.18, "heading": 0.3}
_PDF_TABLE_MIN_COL_MM = 12.0
_PDF_TABLE_PAD_MM = 1.2        # セルの内側の余白
_PDF_TABLE_BORDER_MM = 0.2
_PDF_ASCII_EM = 0.6            # IPAexゴシックの英数字1文字のおおよその幅（文字の大きさに対する比）
_PDF_TABLE_GAP_MM = 1.5        # 表の前後の余白
_PDF_TABLE_MIN_LINES = 2       # 行を分割するとき、1ページに少なくとも置く行数


class OutputFormatError(RuntimeError):
    """出力形式の生成に必要なリソース（フォント等）が利用できない場合のエラー"""


def _pdf_font_path():
    from .path_utils import get_app_dir
    return get_app_dir() / "fonts" / PDF_FONT_FILENAME


def _sanitize_pdf_text(text: str, cmap) -> str:
    """PDFフォントで描画できない文字を置き換える（異体字セレクタ等は除去、未収録文字は「?」）"""
    out = []
    for ch in text:
        cp = ord(ch)
        if cp in (0xFE0F, 0x200D, 0x200B) or (cp < 0x20 and ch != "\t"):
            continue
        if ch == "\t":
            out.append("    ")
        elif cp in cmap:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


Line = List[Tuple[str, bool]]  # 1行分の（文字, 太字か）の並び


def wrap_runs(runs: List[Run], width: float, char_width) -> List[Line]:
    """Run列を幅 width で折り返す。日本語は1文字単位、英数字は単語単位（幅を超える単語は文字単位）。

    char_width は1文字の幅を返す関数。幅が1文字分より狭くても、1行に必ず1文字は置く。"""
    lines: List[Line] = []
    for source in _split_run_lines(runs):
        text = "".join(ch for ch, _ in source)
        line: Line = []
        used = 0.0
        pos = 0
        while pos < len(source):
            m = _WORD_RE.match(text, pos)
            token = source[pos:m.end()] if m else source[pos:pos + 1]
            pos += len(token)
            token_w = sum(char_width(ch) for ch, _ in token)
            if used + token_w <= width + 1e-6:
                line += token
                used += token_w
                continue
            if token[0][0].isspace():
                # 折り返し位置の空白は捨てる（次の行の先頭に空白を残さない）
                lines.append(_strip_tail(line))
                line, used = [], 0.0
                continue
            if token_w <= width and line:
                lines.append(_strip_tail(line))
                line, used = list(token), token_w
                continue
            for item in token:
                w = char_width(item[0])
                if line and used + w > width + 1e-6:
                    lines.append(_strip_tail(line))
                    line, used = [], 0.0
                line.append(item)
                used += w
        lines.append(line)
    return lines


def _strip_tail(line: Line) -> Line:
    """折り返しで行末に来た空白を除く（右揃え・中央揃えの位置がずれないように）"""
    while line and line[-1][0].isspace():
        line = line[:-1]
    return line


def _split_run_lines(runs: List[Run]) -> List[Line]:
    """Run列を "\\n"（セル内改行）で区切った行ごとの文字の並びにする"""
    result: List[Line] = [[]]
    for r in runs:
        for i, part in enumerate(r.text.split("\n")):
            if i:
                result.append([])
            result[-1] += [(ch, r.bold) for ch in part]
    return result


class _PdfTable:
    """PDFの表の割り付けと描画。

    fpdf2 の table() は1ページより高い行で例外になり、擬似太字も混在できないため、自前で描画する。"""

    def __init__(self, pdf, table: Table, cmap):
        self.pdf = pdf
        self.aligns = table.aligns
        n = table.ncols
        col = pdf.epw / n
        self.pad = min(_PDF_TABLE_PAD_MM, col * 0.15)
        self.size = table_font_pt(col, self.pad, _PDF_BODY_PT)
        self.line_mm = self.size * 0.5
        self.widths = column_widths(table, pdf.epw, _PDF_TABLE_MIN_COL_MM, self.size * _PT_MM * _PDF_ASCII_EM)
        pdf.set_font(PDF_FONT_FAMILY, size=self.size)
        cache = {}

        def char_width(ch: str) -> float:
            if ch not in cache:
                cache[ch] = pdf.get_string_width(ch)
            return cache[ch]

        self.char_width = char_width

        def layout(row: List[Cell], bold: bool) -> List[List[Line]]:
            return [wrap_runs([Run("\n".join(_sanitize_pdf_text(part, cmap) for part in r.text.split("\n")),
                                   r.bold or bold) for r in cell], w - 2 * self.pad, char_width)
                    for cell, w in zip(row, self.widths)]

        grid = table.grid()
        self.header = layout(grid[0], True)
        self.rows = [layout(row, False) for row in grid[1:]]
        self.page_mm = pdf.page_break_trigger - pdf.t_margin
        # 見出し行が高すぎると本文を置く場所が無くなるため、半ページを超えるときは繰り返さない
        self.repeat = self._height(self._count(self.header)) <= self.page_mm / 2
        self.capacity = self._fit(self.page_mm - (self._height(self._count(self.header)) if self.repeat else 0))

    @staticmethod
    def _count(row: List[List[Line]]) -> int:
        return max(len(cell) for cell in row)

    def _height(self, lines: int) -> float:
        return lines * self.line_mm + 2 * self.pad

    def _fit(self, height: float) -> int:
        """高さ height に入る行数"""
        return int((height - 2 * self.pad + 1e-6) // self.line_mm)

    def lead_height(self, limit: float = float("inf")) -> float:
        """見出し行と本文の最初の部分が離れないために必要な高さ（表の前の余白を含む）。

        最初の行をまるごと置くと limit を超えるときは、分割して置く高さを返す。"""
        head = self._count(self.header)
        if not self.repeat:
            return _PDF_TABLE_GAP_MM + self._height(_PDF_TABLE_MIN_LINES)
        lead = _PDF_TABLE_GAP_MM + self._height(head)
        if self.rows:
            first = self._count(self.rows[0])
            if first > self.capacity or lead + self._height(first) > limit:
                first = min(first, _PDF_TABLE_MIN_LINES)
            lead += self._height(first)
        return lead

    def draw(self, after_heading: bool) -> None:
        """表を描く。after_heading は直前が見出しか（見出しと表が別のページに離れないよう、最初の行を分割してよい）"""
        pdf = self.pdf
        pdf.set_font(PDF_FONT_FAMILY, size=self.size)
        auto_page_break = pdf.auto_page_break
        pdf.auto_page_break = False
        split_first = False
        try:
            if pdf.get_y() > pdf.t_margin:
                rest = pdf.page_break_trigger - pdf.get_y()
                split_first = after_heading and self.lead_height(rest) <= rest
                if self.lead_height() > rest and not split_first:
                    pdf.add_page()
                else:
                    pdf.set_y(pdf.get_y() + _PDF_TABLE_GAP_MM)
            self.y = pdf.get_y()
            if self.repeat:
                self._draw_lines(self.header, 0, self._count(self.header), True)
            else:
                self._draw_row(self.header, True, split_first)
                split_first = False
            for i, row in enumerate(self.rows):
                self._draw_row(row, False, split_first and i == 0)
            pdf.set_xy(pdf.l_margin, self.y + _PDF_TABLE_GAP_MM)
        finally:
            pdf.auto_page_break = auto_page_break

    def _new_page(self) -> None:
        self.pdf.add_page()
        self.y = self.pdf.t_margin
        if self.repeat:
            self._draw_lines(self.header, 0, self._count(self.header), True)

    def _draw_row(self, row: List[List[Line]], header: bool, split: bool) -> None:
        """1行を描く。残りに入らなければ次のページへ送り、1ページより高い行（または split のとき）は分割する"""
        total = self._count(row)
        start = 0
        while start < total:
            rest = total - start
            fit = self._fit(self.pdf.page_break_trigger - self.y)
            if rest <= fit:
                count = rest
            elif fit < _PDF_TABLE_MIN_LINES or (start == 0 and total <= self.capacity and not split):
                self._new_page()
                continue
            else:
                count = fit
            self._draw_lines(row, start, count, header)
            start += count

    def _draw_lines(self, row: List[List[Line]], start: int, count: int, header: bool) -> None:
        """行のうち start から count 行分を、罫線・背景とともに描く"""
        from fpdf.enums import TextMode

        pdf = self.pdf
        height = self._height(count)
        stroke = _PDF_FAKE_BOLD_MM["body"] * self.size / _PDF_BODY_PT  # 小さな文字がつぶれないよう細くする
        x = pdf.l_margin
        with pdf.local_context(line_width=_PDF_TABLE_BORDER_MM, draw_color=0,
                               fill_color=tuple(bytes.fromhex(TABLE_HEADER_FILL))):
            for w in self.widths:
                pdf.rect(x, self.y, w, height, style="DF" if header else "D")
                x += w
        x = pdf.l_margin
        for cell, w, align in zip(row, self.widths, self.aligns):
            for i, line in enumerate(cell[start:start + count]):
                text_w = sum(self.char_width(ch) for ch, _ in line)
                spare = max(0.0, w - 2 * self.pad - text_w)
                left = x + self.pad + {"center": spare / 2, "right": spare}.get(align, 0.0)
                baseline = self.y + self.pad + self.line_mm * (i + 0.5) + 0.3 * self.size * _PT_MM
                for text, bold in _line_segments(line):
                    if bold:
                        with pdf.local_context(text_mode=TextMode.FILL_STROKE,
                                               line_width=stroke, draw_color=0):
                            pdf.text(left, baseline, text)
                    else:
                        pdf.text(left, baseline, text)
                    left += sum(self.char_width(ch) for ch in text)
            x += w
        self.y += height


def _line_segments(line: Line) -> List[Tuple[str, bool]]:
    """1行を、同じ太さが続く文字列に分ける"""
    segments: List[Tuple[str, bool]] = []
    for ch, bold in line:
        if segments and segments[-1][1] == bold:
            segments[-1] = (segments[-1][0] + ch, bold)
        else:
            segments.append((ch, bold))
    return segments


def blocks_to_pdf(blocks: List[Block]) -> bytes:
    """IRをPDF(A4縦・余白20mm)にする。日本語フォントはfonts/に同梱したIPAexゴシックを埋め込む。

    IPAexゴシックには太字がないため、太字は輪郭線を描画する擬似太字で表現する。"""
    from fpdf import FPDF
    from fpdf.enums import TextMode, WrapMode

    font_path = _pdf_font_path()
    if not font_path.is_file():
        raise OutputFormatError(f"PDF用フォントが見つかりません: {font_path}")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_font(PDF_FONT_FAMILY, "", str(font_path))
    cmap = pdf.fonts[PDF_FONT_FAMILY.lower()].cmap
    pdf.add_page()
    base_margin = pdf.l_margin

    def write_runs(runs: List[Run], size: float, line_mm: float, bold_all: bool, bold_kind: str, pdf=pdf) -> None:
        pdf.set_font(PDF_FONT_FAMILY, size=size)
        for r in runs:
            text = _sanitize_pdf_text(r.text, cmap)
            if not text:
                continue
            # 半角スペースを含む日本語文でスペース位置の折り返しを優先すると行がほぼ空になるため、
            # 日本語を含むrunは文字単位で折り返す（英文のみのrunは単語単位）
            wrap = WrapMode.CHAR if _CJK_RE.search(text) else WrapMode.WORD
            if r.bold or bold_all:
                with pdf.local_context(text_mode=TextMode.FILL_STROKE,
                                       line_width=_PDF_FAKE_BOLD_MM[bold_kind], draw_color=0):
                    pdf.write(line_mm, text, wrapmode=wrap)
            else:
                pdf.write(line_mm, text, wrapmode=wrap)

    layouts = {}

    def table_layout(table: Table) -> _PdfTable:
        if id(table) not in layouts:
            layouts[id(table)] = _PdfTable(pdf, table, cmap)
        return layouts[id(table)]

    def heading_line(heading: Heading) -> float:
        """見出しの1行の高さ"""
        return _PDF_HEADING_PT.get(heading.level, _PDF_HEADING_PT[3]) * 0.5

    page_height = pdf.page_break_trigger - pdf.t_margin
    probe = None  # 折り返す見出しの高さを測るための、出力しない PDF
    heights = {}

    def heading_height(heading: Heading) -> float:
        """見出しの高さ（折り返した行数分）。半ページを超える見出しは、途中で改ページしてよいので1行分とする"""
        nonlocal probe
        line = heading_line(heading)
        if id(heading) in heights:
            return heights[id(heading)]
        size = _PDF_HEADING_PT.get(heading.level, _PDF_HEADING_PT[3])
        pdf.set_font(PDF_FONT_FAMILY, size=size)
        width = sum(pdf.get_string_width(_sanitize_pdf_text(r.text, cmap)) for r in heading.runs)
        height = line
        # 余裕のある1行の見出し（ほとんどの場合）は測らない
        if width > pdf.epw - 4 * pdf.c_margin:
            if probe is None:
                probe = FPDF(orientation="P", unit="mm", format="A4")
                probe.set_margins(20, 20, 20)
                probe.set_auto_page_break(False)
                probe.add_font(PDF_FONT_FAMILY, "", str(font_path))
                probe.add_page()
            probe.set_xy(probe.l_margin, probe.t_margin)
            write_runs(heading.runs, size, line, True, "heading", probe)
            height = probe.get_y() - probe.t_margin + line
            if height > page_height / 2:
                height = line
        heights[id(heading)] = height
        return height

    def heading_minimum(heading: Heading) -> float:
        """見出しと本文2行分の高さ"""
        return heading_height(heading) + _PDF_LINE_MM * 2

    def after_heading(index: int) -> bool:
        """直前（空行を除く）が見出しか"""
        previous = next((b for b in reversed(blocks[max(0, index - 2):index]) if not isinstance(b, Blank)), None)
        return isinstance(previous, Heading)

    def heading_space(index: int) -> Tuple[float, bool]:
        """見出しと、その直後の本文2行分（表が続くときは表の見出し行と最初の行）に必要な高さ。

        見出しが続くときは最後の見出しの後まで含める。合わせて1ページに入らないときは最初の見出しの分だけにする。
        戻り値は（高さ, 続く見出しの分まで含めたか）。"""
        minimum = (heading_minimum(blocks[index]), False)
        page = page_height
        total = heading_height(blocks[index])
        blank = 0.0
        for b in blocks[index + 1:]:
            if isinstance(b, Blank):
                blank = _PDF_LINE_MM / 2
                continue
            if isinstance(b, Heading):
                total += 1 + blank + 3 + heading_height(b)
                blank = 0.0
                if total > page:
                    return minimum
                continue
            if isinstance(b, Table):
                total += 1 + blank
                total += table_layout(b).lead_height(page - total)
                return (total, True) if total <= page else minimum
            break
        total += _PDF_LINE_MM * 2
        return (total, True) if total <= page else minimum

    def last_heading(index: int) -> bool:
        """次（空行を除く）が見出しではないか"""
        following = next((b for b in blocks[index + 1:index + 3] if not isinstance(b, Blank)), None)
        return not isinstance(following, Heading)

    chain_reserved = False  # 続く見出しの分まで、最初の見出しのところで確保したか

    for index, b in enumerate(blocks):
        if isinstance(b, Heading):
            size = _PDF_HEADING_PT.get(b.level, _PDF_HEADING_PT[3])
            line = heading_line(b)
            if pdf.get_y() > pdf.t_margin:
                pdf.ln(3)
            # 見出しだけがページ末尾に残らないよう、直後の本文1〜2行分の余白がなければ改ページ
            # （続く見出しの分は、1ページに入るなら最初の見出しのところでまとめて確保する）
            if after_heading(index) and chain_reserved:
                needed = heading_minimum(b)
            else:
                needed, chain_reserved = heading_space(index)
                # 1ページを超える見出しの連続の途中では、ページを大きく空けてまで残りをまとめない
                if after_heading(index) and not last_heading(index) and needed > page_height / 3:
                    needed, chain_reserved = heading_minimum(b), False
            if pdf.get_y() + needed > pdf.page_break_trigger and pdf.get_y() > pdf.t_margin:
                pdf.add_page()
            page, top = pdf.page, pdf.get_y()
            write_runs(b.runs, size, line, True, "heading")
            # 確保した高さより大きい見出し（半ページを超えるもの）の後は、確保し直す
            if pdf.page != page or pdf.get_y() - top + line > heading_height(b) + 0.01:
                chain_reserved = False
            pdf.ln(line + 1)
        elif isinstance(b, ListItem):
            # 異常に深い入れ子で本文幅が無くなり描画に失敗しないよう、インデントの段数に上限を設ける
            indent = base_margin + _PDF_INDENT_MM * min(b.depth, _PDF_MAX_LIST_DEPTH)
            marker = f"{b.number}." if b.ordered else "・"
            pdf.set_font(PDF_FONT_FAMILY, size=_PDF_BODY_PT)
            marker_w = pdf.get_string_width(marker + " ") if b.ordered else pdf.get_string_width(marker)
            pdf.set_left_margin(indent + marker_w)
            pdf.set_x(indent)
            pdf.write(_PDF_LINE_MM, marker + (" " if b.ordered else ""))
            write_runs(b.runs, _PDF_BODY_PT, _PDF_LINE_MM, False, "body")
            pdf.ln(_PDF_LINE_MM)
            pdf.set_left_margin(base_margin)
            pdf.set_x(base_margin)
        elif isinstance(b, Paragraph):
            write_runs(b.runs, _PDF_BODY_PT, _PDF_LINE_MM, False, "body")
            pdf.ln(_PDF_LINE_MM)
        elif isinstance(b, Table):
            table_layout(b).draw(after_heading(index))
        else:
            pdf.ln(_PDF_LINE_MM / 2)

    return bytes(pdf.output())


def markdown_to_pdf(markdown: str) -> bytes:
    return blocks_to_pdf(parse_markdown(markdown))
