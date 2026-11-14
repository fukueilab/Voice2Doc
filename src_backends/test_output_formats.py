"""output_formats（Markdown→IR→各形式変換）のテスト"""
from .output_formats import (
    Blank,
    Heading,
    ListItem,
    OutputFormatError,
    Paragraph,
    Run,
    markdown_to_docx,
    markdown_to_pdf,
    markdown_to_txt,
    parse_inline,
    parse_markdown,
)


def _text(runs):
    return "".join(r.text for r in runs)


class TestParseInline:
    def test_plain_text(self):
        assert parse_inline("こんにちは") == [Run("こんにちは")]

    def test_bold_split_into_runs(self):
        assert parse_inline("前**太字**後") == [Run("前"), Run("太字", bold=True), Run("後")]

    def test_multiple_bold(self):
        runs = parse_inline("**a**と**b**")
        assert [r.bold for r in runs] == [True, False, True]

    def test_unclosed_bold_marker_removed(self):
        assert _text(parse_inline("途中で切れた**太字")) == "途中で切れた太字"

    def test_italic_and_code_stripped(self):
        assert _text(parse_inline("*斜体*と`code`")) == "斜体とcode"

    def test_link_becomes_text_with_url(self):
        assert _text(parse_inline("[公式](https://example.com)")) == "公式 (https://example.com)"

    def test_empty(self):
        assert parse_inline("") == []

    def test_asterisk_in_math_not_treated_as_italic(self):
        assert _text(parse_inline("2 * 3 * 4")) == "2 * 3 * 4"


class TestParseMarkdown:
    def test_empty_and_none(self):
        assert parse_markdown("") == []
        assert parse_markdown(None) == []
        assert parse_markdown("\n\n \n") == []

    def test_headings_levels(self):
        blocks = parse_markdown("# 大\n## 中\n### 小")
        assert [(b.level, _text(b.runs)) for b in blocks] == [(1, "大"), (2, "中"), (3, "小")]

    def test_heading_requires_space(self):
        blocks = parse_markdown("#タグ")
        assert isinstance(blocks[0], Paragraph)

    def test_paragraph_per_line_and_blank_collapsed(self):
        blocks = parse_markdown("一行目\n二行目\n\n\n\n三行目")
        assert [type(b) for b in blocks] == [Paragraph, Paragraph, Blank, Paragraph]

    def test_crlf_and_lf_mixed(self):
        blocks = parse_markdown("a\r\nb\rc\nd")
        assert [_text(b.runs) for b in blocks] == ["a", "b", "c", "d"]

    def test_bullets(self):
        blocks = parse_markdown("- a\n* b\n+ c")
        assert all(isinstance(b, ListItem) and not b.ordered and b.depth == 0 for b in blocks)
        assert [_text(b.runs) for b in blocks] == ["a", "b", "c"]

    def test_ordered_keeps_number(self):
        blocks = parse_markdown("1. a\n2. b\n3) c")
        assert [(b.ordered, b.number) for b in blocks] == [(True, 1), (True, 2), (True, 3)]

    def test_nested_list_depth_two_space_and_four_space(self):
        for indent in ("  ", "    ", "\t"):
            blocks = parse_markdown(f"- a\n{indent}- b\n{indent}{indent}- c\n- d")
            assert [b.depth for b in blocks] == [0, 1, 2, 0]

    def test_mixed_ordered_and_bullets(self):
        blocks = parse_markdown("1. a\n   - b\n2. c")
        assert [(b.ordered, b.depth) for b in blocks] == [(True, 0), (False, 1), (True, 0)]

    def test_paragraph_after_list_resets_depth(self):
        blocks = parse_markdown("- a\n  - b\n本文\n- c")
        assert blocks[-1].depth == 0

    def test_bold_line_not_mistaken_for_bullet(self):
        blocks = parse_markdown("**重要**です")
        assert isinstance(blocks[0], Paragraph)
        assert blocks[0].runs[0] == Run("重要", bold=True)

    def test_negative_number_not_list(self):
        assert isinstance(parse_markdown("-5℃です")[0], Paragraph)

    def test_blockquote_becomes_paragraph(self):
        blocks = parse_markdown("> 引用です")
        assert isinstance(blocks[0], Paragraph) and _text(blocks[0].runs) == "引用です"

    def test_code_block_lines_kept_without_fence(self):
        blocks = parse_markdown("```python\nx = 1\n\ny = **2**\n```")
        texts = [_text(b.runs) for b in blocks if isinstance(b, Paragraph)]
        assert texts == ["x = 1", "y = **2**"]

    def test_horizontal_rule_becomes_blank(self):
        blocks = parse_markdown("前\n---\n後")
        assert [type(b) for b in blocks] == [Paragraph, Blank, Paragraph]

    def test_table_rows_joined(self):
        blocks = parse_markdown("| 項目 | 値 |\n|---|---|\n| a | 1 |")
        assert [_text(b.runs) for b in blocks] == ["項目 | 値", "a | 1"]

    def test_heading_trailing_hashes_removed(self):
        assert _text(parse_markdown("## 見出し ##")[0].runs) == "見出し"

    def test_only_heading(self):
        blocks = parse_markdown("# タイトル")
        assert len(blocks) == 1 and isinstance(blocks[0], Heading)

    def test_leading_trailing_blanks_trimmed(self):
        blocks = parse_markdown("\n\n本文\n\n")
        assert len(blocks) == 1


class TestMarkdownToTxt:
    def _txt(self, md):
        return markdown_to_txt(md).decode("utf-8-sig")

    def test_bom_and_crlf(self):
        data = markdown_to_txt("# 見出し\n本文")
        assert data.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")

    def test_no_markdown_symbols_remain(self):
        out = self._txt("# 報告書\n## 概要\n**重要**な点\n- 項目A\n- 項目B")
        for sym in ("#", "**"):
            assert sym not in out

    def test_heading_surrounded_by_blank_lines(self):
        out = self._txt("前文\n# 見出し\n本文")
        assert out == "前文\r\n\r\n見出し\r\n\r\n本文\r\n"

    def test_heading_first_has_no_leading_blank(self):
        assert self._txt("# 見出し\n本文") == "見出し\r\n\r\n本文\r\n"

    def test_bullets_become_nakaguro_with_nested_indent(self):
        out = self._txt("- a\n  - b")
        assert out == "・a\r\n　・b\r\n"

    def test_ordered_list_keeps_numbers(self):
        assert self._txt("1. a\n2. b") == "1. a\r\n2. b\r\n"

    def test_consecutive_headings_no_double_blank(self):
        out = self._txt("# A\n## B\n本文")
        assert "\r\n\r\n\r\n" not in out

    def test_empty_input(self):
        assert markdown_to_txt("").decode("utf-8-sig") == "\r\n"

    def test_blank_line_between_paragraphs_preserved(self):
        assert self._txt("a\n\nb") == "a\r\n\r\nb\r\n"


class TestMarkdownToDocx:
    def _doc(self, md):
        import io

        from docx import Document
        return Document(io.BytesIO(markdown_to_docx(md)))

    def test_is_valid_zip_docx(self):
        data = markdown_to_docx("本文")
        assert data[:2] == b"PK"

    def test_heading_styles_mapped(self):
        doc = self._doc("# A\n## B\n### C\n#### D")
        assert [p.style.name for p in doc.paragraphs] == ["Heading 1", "Heading 2", "Heading 3", "Heading 3"]

    def test_japanese_text_preserved(self):
        doc = self._doc("# 報告書\n本日の作業内容です")
        assert [p.text for p in doc.paragraphs] == ["報告書", "本日の作業内容です"]

    def test_bold_run(self):
        doc = self._doc("前**太字**後")
        runs = doc.paragraphs[0].runs
        assert [(r.text, bool(r.bold)) for r in runs] == [("前", False), ("太字", True), ("後", False)]

    def test_bullets_use_list_styles_by_depth(self):
        doc = self._doc("- a\n  - b\n    - c\n      - d")
        assert [p.style.name for p in doc.paragraphs] == [
            "List Bullet", "List Bullet 2", "List Bullet 3", "List Bullet 3"]

    def test_ordered_numbers_kept_in_text_across_lists(self):
        doc = self._doc("1. a\n2. b\n\n本文\n\n1. c")
        texts = [p.text for p in doc.paragraphs]
        assert texts == ["1. a", "2. b", "本文", "1. c"]
        assert doc.paragraphs[0].paragraph_format.first_line_indent < 0

    def test_east_asia_font_set_on_normal(self):
        from docx.oxml.ns import qn
        doc = self._doc("本文")
        rfonts = doc.styles["Normal"].element.rPr.rFonts
        assert rfonts.get(qn("w:eastAsia")) == "游ゴシック"

    def test_heading_theme_font_attributes_removed(self):
        from docx.oxml.ns import qn
        doc = self._doc("# A")
        rfonts = doc.styles["Heading 1"].element.rPr.rFonts
        assert rfonts.get(qn("w:asciiTheme")) is None
        assert rfonts.get(qn("w:eastAsia")) == "游ゴシック"

    def test_empty_input_produces_openable_document(self):
        assert self._doc("").paragraphs == []

    # ---- 文字色は全て黒 / H1・H2 は太字 ----
    _USED_STYLES = ("Normal", "Heading 1", "Heading 2", "Heading 3",
                    "List Bullet", "List Bullet 2", "List Bullet 3", "List Paragraph")

    def test_all_used_styles_are_black_without_theme_color(self):
        from docx.oxml.ns import qn
        from docx.shared import RGBColor
        doc = self._doc("# A\n## B\n### C\n本文\n- x")
        for name in self._USED_STYLES:
            style = doc.styles[name]
            color = style.font.color
            # 色指定が無い(=自動色=黒)か、RGB(0,0,0)であること。青系のテーマ色は不可
            assert color.type is None or color.rgb == RGBColor(0, 0, 0), name
            assert color.theme_color is None, name
            rpr = style.element.rPr
            color_el = rpr.find(qn("w:color")) if rpr is not None else None
            if color_el is not None:
                assert color_el.get(qn("w:themeColor")) is None, name
                assert color_el.get(qn("w:val")) in ("000000", "auto"), name

    def test_headings_are_explicitly_black(self):
        from docx.shared import RGBColor
        doc = self._doc("# A")
        assert doc.styles["Heading 1"].font.color.rgb == RGBColor(0, 0, 0)
        assert doc.styles["Heading 2"].font.color.rgb == RGBColor(0, 0, 0)

    def test_runs_have_no_color_override(self):
        doc = self._doc("# H1\n## H2\n本文**太字**\n- 項目\n1. 番号")
        for p in doc.paragraphs:
            for r in p.runs:
                assert r.font.color.type is None

    def test_h1_and_h2_are_bold_in_style_and_runs(self):
        doc = self._doc("# タイトル\n## 小見出し\n本文")
        h1, h2, body = doc.paragraphs
        assert doc.styles["Heading 1"].font.bold is True
        assert doc.styles["Heading 2"].font.bold is True
        assert all(r.bold is True for r in h1.runs)
        assert all(r.bold is True for r in h2.runs)
        # 本文は太字ではない
        assert not any(r.bold for r in body.runs)
        assert not doc.styles["Normal"].font.bold

    def test_h1_bold_applies_even_when_run_has_no_inline_bold(self):
        doc = self._doc("# 通常の見出し")
        assert [r.bold for r in doc.paragraphs[0].runs] == [True]

    def test_no_markdown_symbols_remain(self):
        doc = self._doc("# 見出し\n**太字**\n- 項目")
        assert not any("#" in p.text or "*" in p.text for p in doc.paragraphs)


class TestMarkdownToPdf:
    def _reader(self, md):
        import io

        from pypdf import PdfReader
        return PdfReader(io.BytesIO(markdown_to_pdf(md)))

    def _all_text(self, reader):
        return "".join(page.extract_text() for page in reader.pages)

    def test_is_pdf(self):
        assert markdown_to_pdf("# 見出し\n本文").startswith(b"%PDF")

    def test_japanese_text_extractable(self):
        text = self._all_text(self._reader("# 報告書\n本日の作業内容です"))
        assert "報告書" in text and "本日の作業内容です" in text

    def test_no_markdown_symbols_remain(self):
        text = self._all_text(self._reader("# 見出し\n**太字**です\n- 項目"))
        assert "#" not in text and "*" not in text
        assert "・項目" in text.replace(" ", "")

    def test_long_text_paginates(self):
        md = "\n".join(f"{i}行目の文章です。" * 5 for i in range(200))
        assert len(self._reader(md).pages) > 1

    def test_very_long_unbroken_line_wraps_without_error(self):
        reader = self._reader("あ" * 3000)
        assert len(reader.pages) >= 1

    def test_unsupported_glyphs_replaced_not_fatal(self):
        text = self._all_text(self._reader("完了✅ 😀 テスト"))
        assert "完了" in text and "テスト" in text

    def test_empty_input_produces_valid_pdf(self):
        assert len(self._reader("").pages) == 1

    def test_nested_and_ordered_lists(self):
        text = self._all_text(self._reader("1. a\n  - b\n2. c"))
        assert "1." in text and "2." in text

    def test_heading_not_orphaned_at_page_bottom(self):
        # ページ末尾付近に見出しが来る長さの本文を作り、見出しと直後の本文が同じページに載ること
        body = "\n".join("本文の行です。" for _ in range(38))
        reader = self._reader(f"{body}\n# 末尾の見出し\n直後の本文")
        pages = [p.extract_text() for p in reader.pages]
        page_with_heading = next(t for t in pages if "末尾の見出し" in t)
        assert "直後の本文" in page_with_heading

    def test_missing_font_raises_clear_error(self, monkeypatch, tmp_path):
        import pytest

        from . import output_formats
        monkeypatch.setattr(output_formats, "_pdf_font_path", lambda: tmp_path / "none.ttf")
        with pytest.raises(OutputFormatError, match="フォント"):
            markdown_to_pdf("本文")


class TestReviewRegressions:
    def test_bold_italic_triple_asterisks_become_bold(self):
        runs = parse_inline("***重要***です")
        assert runs[0] == Run("重要", bold=True) and _text(runs) == "重要です"
        assert "*" not in markdown_to_txt("***重要***").decode("utf-8-sig")

    def test_escaped_asterisk_and_underscore_unescaped(self):
        assert _text(parse_inline(r"\*注\* と snake\_case")) == "*注* と snake_case"

    def test_heading_hash_without_space_kept(self):
        assert _text(parse_markdown("# C#")[0].runs) == "C#"
        assert _text(parse_markdown("# 見出し#")[0].runs) == "見出し#"
        assert _text(parse_markdown("## 見出し ##")[0].runs) == "見出し"

    def test_xml_invalid_control_chars_removed_and_docx_ok(self):
        assert _text(parse_markdown("a\x0cb\x0bc\x00d")[0].runs) == "abcd"
        assert markdown_to_docx("a\x0cb\x07c\x1bd")[:2] == b"PK"

    def test_pdf_wraps_japanese_by_char_even_with_ascii_spaces(self):
        import io

        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(markdown_to_pdf("Gemma 4 を使って" + "あ" * 120)))
        lines = [ln for ln in reader.pages[0].extract_text().splitlines() if ln.strip()]
        assert len(lines[0]) > 20

    def test_pdf_very_deep_list_does_not_crash(self):
        md = "\n".join("  " * i + "- item" for i in range(40))
        assert markdown_to_pdf(md).startswith(b"%PDF")
