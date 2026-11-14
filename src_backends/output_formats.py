"""履歴のMarkdownを txt / docx / PDF へ変換する出力形式モジュール。

Markdownを一度、見出し・段落・リスト・太字からなる中間データ（IR）に解析し、
そこから各形式のファイルを生成する。Markdown記号は利用者に見せない。
"""
import re
from dataclasses import dataclass, field
from typing import List, Union


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


Block = Union[Heading, Paragraph, ListItem, Blank]

# 末尾の閉じ「#」は直前に空白がある場合だけ除去する（「# C#」の「#」は本文として残す）
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+・]\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_QUOTE_RE = re.compile(r"^\s*(?:>\s?)+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"\*(?!\s)([^*]+?)(?<!\s)\*")
_CODE_RE = re.compile(r"`([^`]*)`")
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
# バックスラッシュエスケープ（\* \_）は解析中だけ私用領域の文字に退避し、最後に元の記号へ戻す
_ESCAPES = {"\\*": "", "\\_": ""}
_UNESCAPES = {"": "*", "": "_"}
# XML 1.0で使えない制御文字（docx生成が失敗するため解析の入口で除去する）
_XML_INVALID_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


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


def parse_markdown(text: str) -> List[Block]:
    """MarkdownをIR（Blockのリスト）に解析する"""
    blocks: List[Block] = []
    if not text:
        return blocks
    text = _XML_INVALID_RE.sub("", text)

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

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()

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
        else:
            blank()
    while lines and lines[-1] == "":
        lines.pop()
    return ("\r\n".join(lines) + "\r\n").encode("utf-8-sig")


def markdown_to_txt(markdown: str) -> bytes:
    return blocks_to_txt(parse_markdown(markdown))


# ==================== docx ====================

DOCX_FONT = "游ゴシック"
_DOCX_HEADING_MAX = 3
_DOCX_LIST_MAX_DEPTH = 3
# Markdownの # と ## に対応する見出し(Word側のレベル)。ボールドにする
_DOCX_BOLD_HEADING_MAX = 2


def _set_docx_font(style, name: str) -> None:
    """スタイルのフォントを（日本語=eastAsia含めて）固定する。テーマフォント指定は上書きされるため除去する"""
    style.font.name = name
    rfonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        rfonts.attrib.pop(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{attr}", None)
    rfonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", name)


def blocks_to_docx(blocks: List[Block]) -> bytes:
    """IRをWord(.docx)にする。見出し→見出し1〜3、箇条書き→箇条書きスタイル、太字→太字。

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

    for b in blocks:
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
        # Blank: Wordは段落間隔で区切られるため出力しない

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

    def write_runs(runs: List[Run], size: float, line_mm: float, bold_all: bool, bold_kind: str) -> None:
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

    for b in blocks:
        if isinstance(b, Heading):
            size = _PDF_HEADING_PT.get(b.level, _PDF_HEADING_PT[3])
            line = size * 0.5
            if pdf.get_y() > pdf.t_margin:
                pdf.ln(3)
            # 見出しだけがページ末尾に残らないよう、直後の本文1〜2行分の余白がなければ改ページ
            if pdf.get_y() + line + _PDF_LINE_MM * 2 > pdf.page_break_trigger:
                pdf.add_page()
            write_runs(b.runs, size, line, True, "heading")
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
        else:
            pdf.ln(_PDF_LINE_MM / 2)

    return bytes(pdf.output())


def markdown_to_pdf(markdown: str) -> bytes:
    return blocks_to_pdf(parse_markdown(markdown))
