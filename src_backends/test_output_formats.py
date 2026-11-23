"""output_formats（Markdown→IR→各形式変換）のテスト"""
import io

import pytest

from .output_formats import (
    Blank,
    Heading,
    ListItem,
    OutputFormatError,
    Paragraph,
    Run,
    Table,
    column_widths,
    display_width,
    markdown_to_docx,
    markdown_to_pdf,
    markdown_to_txt,
    parse_inline,
    parse_markdown,
    wrap_runs,
)


def _text(runs):
    return "".join(r.text for r in runs)


def _grids(blocks):
    """表ブロックだけを、文字列の二次元配列（1行目が見出し）にする"""
    return [[[_text(c) for c in row] for row in b.grid()] for b in blocks if isinstance(b, Table)]


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

    def test_table_becomes_table_block(self):
        blocks = parse_markdown("| 項目 | 値 |\n|---|---|\n| a | 1 |")
        assert _grids(blocks) == [[["項目", "値"], ["a", "1"]]]

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


class TestParseTable:
    def _one(self, md):
        blocks = parse_markdown(md)
        assert [type(b) for b in blocks] == [Table]
        return blocks[0]

    @pytest.mark.parametrize("md", [
        "| a | b |\n|---|---|\n| 1 | 2 |",
        "a | b\n---|---\n1 | 2",
        "| a | b\n|---|---\n| 1 | 2",
        "a | b |\n---|---|\n1 | 2 |",
        "| a | b |\n|-|-|\n| 1 | 2 |",
        "|a|b|\n| - | :-: |\n|1|2|",
        "| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n",
        "  | a | b |\n  |---|---|\n  | 1 | 2 |",
    ])
    def test_notations_accepted(self, md):
        assert _grids([self._one(md)]) == [[["a", "b"], ["1", "2"]]]

    def test_alignments(self):
        t = self._one("|a|b|c|d|\n|:-|:-:|-:|-|\n|1|2|3|4|")
        assert t.aligns == ["left", "center", "right", "left"]

    def test_escaped_pipe_is_text(self):
        assert _grids([self._one("| a \\| b | c |\n|---|---|\n| 1 | x\\|y |")]) == [[["a | b", "c"], ["1", "x|y"]]]

    def test_br_becomes_newline(self):
        t = self._one("| a | b |\n|---|---|\n| 1<br>2<br/>3<BR />4 | **x<br>y** |")
        assert _text(t.rows[0][0]) == "1\n2\n3\n4"
        assert t.rows[0][1] == [Run("x\ny", bold=True)]

    def test_bold_cell(self):
        t = self._one("| a | b |\n|---|---|\n| **強** | 前**中**後 |")
        assert t.rows[0][0] == [Run("強", bold=True)]
        assert t.rows[0][1] == [Run("前"), Run("中", bold=True), Run("後")]

    def test_column_count_is_the_maximum_and_extra_cells_are_kept(self):
        t = self._one("| a | b |\n|---|:-:|\n| 1 |\n| 1 | 2 | 3 |")
        assert t.ncols == 3 and len(t.aligns) == 3
        assert _grids([t]) == [[["a", "b", ""], ["1", "", ""], ["1", "2", "3"]]]

    def test_empty_cells_are_kept(self):
        assert _grids([self._one("| | b |\n|---|---|\n| 1 | |\n||2|")]) == [[["", "b"], ["1", ""], ["", "2"]]]

    def test_dash_cells_in_the_body_are_data(self):
        assert _grids([self._one("| a | b |\n|---|---|\n| - | - |")]) == [[["a", "b"], ["-", "-"]]]

    def test_repeated_separator_rows_in_the_body_are_dropped(self):
        t = self._one("|a|b|\n|---|---|\n|1|2|\n|---|---|\n|--|--|\n|3|4|")
        assert _grids([t]) == [[["a", "b"], ["1", "2"], ["3", "4"]]]

    def test_single_column_and_header_only(self):
        assert _grids([self._one("| a |\n|---|\n| 1 |")]) == [[["a"], ["1"]]]
        assert _grids([self._one("| a | b |\n|---|---|")]) == [[["a", "b"]]]

    def test_table_under_a_list_item(self):
        blocks = parse_markdown("- 項目\n  | a | b |\n  |---|---|\n  | 1 | 2 |\n- 次")
        assert [type(b) for b in blocks] == [ListItem, Table, ListItem]
        assert blocks[2].depth == 0

    def test_table_right_after_a_paragraph_and_before_text(self):
        blocks = parse_markdown("前文\n| a | b |\n|---|---|\n| 1 | 2 |\n後文")
        assert [type(b) for b in blocks] == [Paragraph, Table, Paragraph]

    def test_two_tables(self):
        md = "| a | b |\n|---|---|\n| 1 | 2 |\n\n| c | d |\n|---|---|\n| 3 | 4 |"
        assert [type(b) for b in parse_markdown(md)] == [Table, Blank, Table]

    @pytest.mark.parametrize("after", ["# 見出し", "- 項目 | x", "1. 番号 | x", "> 引用 | x", "```"])
    def test_other_blocks_end_the_table(self, after):
        blocks = parse_markdown(f"| a | b |\n|---|---|\n| 1 | 2 |\n{after}")
        assert _grids(blocks) == [[["a", "b"], ["1", "2"]]]

    def test_code_fence_is_not_a_table(self):
        blocks = parse_markdown("```\n| a | b |\n|---|---|\n| 1 | 2 |\n```")
        assert [_text(b.runs) for b in blocks] == ["| a | b |", "|---|---|", "| 1 | 2 |"]

    def test_rows_without_a_separator_stay_paragraphs(self):
        blocks = parse_markdown("| a | b |\n| 1 | 2 |")
        assert [_text(b.runs) for b in blocks] == ["a | b", "1 | 2"]
        assert isinstance(parse_markdown("売上 | 利益")[0], Paragraph)

    @pytest.mark.parametrize("md", ["# a | b\n---|---", "- a | b\n---|---", "> a | b\n---|---", "a | b\n---"])
    def test_not_started_from_other_blocks(self, md):
        assert not _grids(parse_markdown(md))

    def test_separator_line_cannot_be_the_header(self):
        blocks = parse_markdown("|---|---|\n| a | b |\n|---|---|\n| 1 | 2 |")
        assert _grids(blocks) == [[["a", "b"], ["1", "2"]]]


class TestTableTxt:
    def _txt(self, md):
        return markdown_to_txt(md).decode("utf-8-sig")

    def test_output_is_unchanged(self):
        md = "前\n| 項目 | 値 |\n|---|---|\n| **a** | 1 |\n| b |\n| c | |\n\n後"
        assert self._txt(md) == "前\r\n項目 | 値\r\na | 1\r\nb\r\nc | \r\n\r\n後\r\n"

    def test_newline_in_a_cell_becomes_a_space(self):
        assert self._txt("| a | b |\n|---|---|\n| 1<br>2 | 3 |") == "a | b\r\n1 2 | 3\r\n"


class TestColumnWidths:
    def test_display_width_counts_full_width_as_two(self):
        assert display_width("abc") == 3 and display_width("あいう") == 6
        assert display_width("ab\nあいう") == 6

    def test_widths_sum_to_the_total_and_follow_the_text(self):
        t = parse_markdown("| 項目 | 説明 |\n|---|---|\n| 甲 | とても長い説明の文章です |")[0]
        w = column_widths(t, 170.0, 15.0)
        assert sum(w) == pytest.approx(170.0) and w[0] < w[1] and min(w) >= 15.0

    def test_one_very_long_cell_does_not_starve_other_columns(self):
        t = parse_markdown("|a|b|c|\n|-|-|-|\n|1|%s|3|" % ("長" * 2000))[0]
        w = column_widths(t, 170.0, 15.0)
        assert sum(w) == pytest.approx(170.0) and min(w) >= 15.0

    def test_short_cells_and_dates_keep_their_width(self):
        t = parse_markdown("|番号|日付|内容|\n|-|-|-|\n|行22|2026-11-23|%s|" % ("長" * 100))[0]
        w = column_widths(t, 170.0, 12.0, 2.0)
        assert w[0] >= 6 * 2.0 and w[1] >= 12 * 2.0 and sum(w) == pytest.approx(170.0)

    def test_many_columns_are_equal_when_the_minimum_does_not_fit(self):
        t = parse_markdown("|" + "a|" * 40 + "\n|" + "-|" * 40)[0]
        w = column_widths(t, 170.0, 15.0)
        assert len(w) == 40 and sum(w) == pytest.approx(170.0) and min(w) > 0

    def test_all_empty_cells(self):
        assert sum(column_widths(parse_markdown("| | |\n|-|-|")[0], 100.0, 10.0)) == pytest.approx(100.0)


class TestWrapRuns:
    def _wrap(self, text, width, bold=False):
        return ["".join(ch for ch, _ in line) for line in wrap_runs([Run(text, bold)], width, lambda ch: 1.0)]

    def test_words_are_kept_together_and_spaces_at_the_break_are_dropped(self):
        assert self._wrap("alpha bravo charlie", 6) == ["alpha", "bravo", "charli", "e"]
        assert self._wrap("alpha bravo", 5) == ["alpha", "bravo"]
        assert self._wrap("ab cd ef", 5) == ["ab cd", "ef"]

    def test_japanese_breaks_at_any_character(self):
        assert self._wrap("日本語の文章", 4) == ["日本語の", "文章"]
        assert self._wrap("日本abc語", 4) == ["日本", "abc語"]

    def test_newlines_and_empty(self):
        assert self._wrap("a\n\nb", 5) == ["a", "", "b"]
        assert wrap_runs([], 5, lambda ch: 1.0) == [[]]

    def test_at_least_one_character_per_line(self):
        assert self._wrap("ab漢", 0.5) == ["a", "b", "漢"]

    def test_bold_is_kept_and_a_word_may_change_weight_inside(self):
        lines = wrap_runs([Run("x ab"), Run("cd", True)], 4, lambda ch: 1.0)
        assert lines == [[("x", False)], [("a", False), ("b", False), ("c", True), ("d", True)]]


class TestTableDocx:
    MD = "前文\n\n| 項目 | 値 | 備考 |\n|:--|:-:|--:|\n| a | **太** | 1<br>2 |\n| b |\n\n後文"

    def _doc(self, md):
        from docx import Document
        return Document(io.BytesIO(markdown_to_docx(md)))

    def _cells(self, table):
        return [[c.text for c in row.cells] for row in table.rows]

    def test_table_content(self):
        doc = self._doc(self.MD)
        assert [self._cells(t) for t in doc.tables] == [[["項目", "値", "備考"], ["a", "太", "1\n2"], ["b", "", ""]]]
        assert doc.tables[0].style.name == "Table Grid"
        assert [p.text for p in doc.paragraphs] == ["前文", "後文"]
        assert not any("|" in p.text for p in doc.paragraphs)

    def test_header_row_is_bold_shaded_and_repeated(self):
        from docx.oxml.ns import qn
        t = self._doc(self.MD).tables[0]
        head, body = t.rows[0], t.rows[1]
        assert all(r.bold for c in head.cells for p in c.paragraphs for r in p.runs)
        assert [c._tc.tcPr.find(qn("w:shd")).get(qn("w:fill")) for c in head.cells] == ["F2F2F2"] * 3
        assert head._tr.trPr.find(qn("w:tblHeader")) is not None
        assert all(c._tc.tcPr.find(qn("w:shd")) is None for c in body.cells)
        assert body._tr.trPr is None or body._tr.trPr.find(qn("w:tblHeader")) is None
        assert [bool(r.bold) for c in body.cells for r in c.paragraphs[0].runs] == [False, True, False]

    def test_short_rows_are_not_split_but_tall_rows_can_be(self):
        from docx.oxml.ns import qn
        t = self._doc("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | %s |" % ("長" * 3000)).tables[0]
        flags = [row._tr.trPr is not None and row._tr.trPr.find(qn("w:cantSplit")) is not None for row in t.rows]
        assert flags == [True, True, False]
        assert all(c.paragraphs[0].paragraph_format.keep_with_next for c in t.rows[0].cells)

    def test_crowded_table_uses_small_text_and_margins(self):
        from docx.oxml.ns import qn
        md = "|" + "列|" * 40 + "\n|" + "-|" * 40 + "\n|" + "値|" * 40
        t = self._doc(md).tables[0]
        assert t.rows[1].cells[0].paragraphs[0].runs[0].font.size.pt == 5
        tags = [e.tag.split("}")[1] for e in t._tbl.tblPr]
        assert tags.index("tblLayout") + 1 == tags.index("tblCellMar") < tags.index("tblLook")
        # 普通の表は既定の文字の大きさ・余白のまま
        t = self._doc(self.MD).tables[0]
        assert t._tbl.tblPr.find(qn("w:tblCellMar")) is None
        assert t.rows[1].cells[0].paragraphs[0].runs[0].font.size is None

    def test_grid_borders_are_black(self):
        from docx.oxml.ns import qn
        doc = self._doc(self.MD)
        borders = doc.styles["Table Grid"].element.find(qn("w:tblPr")).find(qn("w:tblBorders"))
        assert len(borders) == 6
        assert all(b.get(qn("w:color")) in ("auto", "000000") and b.get(qn("w:val")) == "single" for b in borders)

    def test_schema_order_of_cell_properties(self):
        """w:shd は w:tcW の後に置く（順序が崩れると Word が修復を求める）"""
        t = self._doc(self.MD).tables[0]
        assert [c.tag.split("}")[1] for c in t.rows[0].cells[0]._tc.tcPr] == ["tcW", "shd"]

    def test_alignment(self):
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A
        t = self._doc(self.MD).tables[0]
        for row in t.rows:
            assert [c.paragraphs[0].alignment for c in row.cells] in ([None, A.CENTER, A.RIGHT],
                                                                      [A.LEFT, A.CENTER, A.RIGHT])

    def test_runs_have_no_color(self):
        t = self._doc(self.MD).tables[0]
        assert all(r.font.color.type is None for row in t.rows for c in row.cells for p in c.paragraphs for r in p.runs)

    def test_widths_fill_the_page_and_are_fixed(self):
        doc = self._doc(self.MD)
        t, s = doc.tables[0], doc.sections[0]
        body = s.page_width - s.left_margin - s.right_margin
        assert t.autofit is False
        assert abs(sum(c.width for c in t.columns) - body) < 5000
        assert [c.width for c in t.rows[1].cells] == [c.width for c in t.columns]

    def _body_tags(self, doc):
        return [e.tag.split("}")[1] for e in doc.element.body]

    @pytest.mark.parametrize("gap", ["\n\n", "\n---\n"])
    def test_consecutive_tables_stay_separate(self, gap):
        one = "| a | b |\n|---|---|\n| 1 | 2 |"
        doc = self._doc(one + gap + one)
        assert len(doc.tables) == 2
        assert self._body_tags(doc) == ["tbl", "p", "tbl", "p", "sectPr"]

    def test_document_ending_with_a_table_has_a_last_paragraph(self):
        assert self._body_tags(self._doc("| a |\n|---|\n| 1 |")) == ["tbl", "p", "sectPr"]

    def test_no_extra_paragraph_when_text_follows(self):
        assert self._body_tags(self._doc("| a |\n|---|\n| 1 |\n本文")) == ["tbl", "p", "sectPr"]

    def test_columns_over_the_word_limit_are_merged_into_the_last_one(self):
        """Word の表は63列まで。超える分は最後の列へまとめ、文字は捨てない"""
        md = "|" + "列|" * 70 + "\n|" + "-|" * 70 + "\n|" + "値|" * 70
        t = self._doc(md).tables[0]
        assert len(t.columns) == 63
        assert t.rows[1].cells[-1].text == " | ".join(["値"] * 8)


class TestTablePdf:
    def _pages(self, md):
        from pypdf import PdfReader
        return [p.extract_text() for p in PdfReader(io.BytesIO(markdown_to_pdf(md))).pages]

    def _flat(self, md):
        return "".join("".join(self._pages(md)).split())

    def test_cells_are_drawn_without_pipes(self):
        text = self._flat("前文\n\n| 項目 | 値 |\n|---|---|\n| りんご | **百円** |\n\n後文")
        assert "|" not in text and "--" not in text
        for word in ("前文", "項目", "値", "りんご", "百円", "後文"):
            assert word in text

    def test_grid_and_header_background(self):
        import re

        from pypdf import PdfReader
        pdf = markdown_to_pdf("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
        data = PdfReader(io.BytesIO(pdf)).pages[0].get_contents().get_data().decode("latin-1")
        assert len(re.findall(r" re\s+[SBfFb]", data)) >= 6
        assert "0.949 0.949 0.949 rg" in data  # F2F2F2 = 242/255
        assert "2 Tr" in data  # 見出しの擬似太字

    def test_header_is_repeated_on_every_page(self):
        rows = "\n".join(f"| 行{i} | 内容 |" for i in range(120))
        pages = self._pages(f"| 見出列 | 説明列 |\n|---|---|\n{rows}")
        assert len(pages) > 2
        assert all(p.count("見出列") == 1 and "説明列" in p for p in pages)
        text = "".join(pages)
        assert all(f"行{i}" in text for i in range(120))

    def test_header_is_not_left_alone_at_the_page_bottom(self):
        for n in range(32, 42):
            body = "\n".join("本文の行です。" for _ in range(n))
            pages = self._pages(f"{body}\n| 見出列 | 説明列 |\n|---|---|\n| 最初 | 内容 |")
            page = next(p for p in pages if "見出列" in p)
            assert "最初" in page, n
            assert sum("見出列" in p for p in pages) == 1, n

    def test_heading_stays_with_the_table(self):
        for n in range(28, 42):
            body = "\n".join("本文の行です。" for _ in range(n))
            pages = self._pages(f"{body}\n# 表の題\n\n| 見出列 | 説明列 |\n|---|---|\n| 最初 | 内容 |")
            page = next(p for p in pages if "表の題" in p)
            assert "最初" in page, n

    @pytest.mark.parametrize("heads", [
        "## 親の題\n### 子の題",
        "## 親の題\n\n### 子の題",
        "# 親の題\n## 中の題\n### 子の題",
    ])
    @pytest.mark.parametrize("cell", ["内容", "あ" * 600, "長" * 5000])
    def test_consecutive_headings_stay_with_the_table(self, heads, cell):
        for n in range(16, 42, 1 if len(cell) < 10 else 3):
            body = "\n".join("本文の行です。" for _ in range(n))
            pages = self._pages(f"{body}\n{heads}\n\n| 見出列 | 説明列 |\n|---|---|\n| 最初 | {cell} |")
            page = next(p for p in pages if "親の題" in p)
            assert "子の題" in page and "見出列" in page and "最初" in page, n

    def test_consecutive_headings_stay_with_the_text(self):
        for n in range(24, 42):
            pages = self._pages("本文の行です。\n" * n + "# 親の題\n## 子の題\n直後の文")
            assert "直後の文" in next(p for p in pages if "親の題" in p), n

    def test_many_headings_do_not_waste_pages(self):
        assert len(self._pages("\n".join(f"# 見出し{i}" for i in range(60)))) <= 5

    @pytest.mark.parametrize("lines", range(40, 52))
    @pytest.mark.parametrize("where", ["header", "row"])
    def test_heading_is_not_left_alone_before_a_row_as_tall_as_a_page(self, lines, where):
        tall = "<br>".join(f"z{i}" for i in range(lines))
        head, row = (tall, "x") if where == "header" else ("見出列", tall)
        pages = self._pages(f"# 題\n\n| a | {head} |\n|---|---|\n| x | {row} |\n| 次 | 次 |")
        assert "z0" in pages[0]
        assert "".join(pages).count("z") == lines

    def test_row_taller_than_a_page_is_split(self):
        long = "".join(f"{i:04d}番" for i in range(1500))
        pages = self._pages(f"| 見出列 | 説明列 |\n|---|---|\n| 甲 | {long} |\n| 乙 | 終わり |\n\n後文")
        assert len(pages) > 1 and all("見出列" in p for p in pages)
        text = "".join("".join(pages).split())
        assert "終わり" in text and "後文" in text and text.count("番") == 1500

    def test_header_taller_than_half_a_page_is_not_repeated(self):
        rows = "\n".join(f"| 行{i} | x |" for i in range(80))
        text = self._flat(f"| {'頭' * 3000} | b |\n|---|---|\n{rows}")
        assert text.count("頭") == 3000 and "行79" in text

    @pytest.mark.parametrize("cols", [1, 10, 40, 200])
    def test_any_number_of_columns(self, cols):
        md = "|" + "見出し|" * cols + "\n|" + "-|" * cols + ("\n|" + "値あ1|" * cols) * 3
        assert self._flat(md).count("値") == cols * 3

    def test_long_word_emoji_control_and_br(self):
        word = "Supercalifragilistic" * 20
        text = self._flat(f"| a | b | c |\n|---|---|---|\n| {word} | 完了✅😀\t | 上<br>下 |\n| | | |")
        assert "完了" in text and "上" in text and "下" in text and text.count("S") == 20

    def test_japanese_wraps_by_character_and_english_by_word(self):
        pages = self._pages("| k | v |\n|---|---|\n| 1 | " + "日本語の長い文章。" * 20 + " |\n| 2 | "
                            + "alpha bravo " * 60 + "|")
        text = "".join(pages)
        assert "".join(text.split()).count("日本語の長い文章。") == 20
        assert text.count("alpha") == 60 and text.count("bravo") == 60

    def test_text_after_the_table_paginates_normally(self):
        rows = "\n".join(f"| {i} | x |" for i in range(60))
        pages = self._pages(f"| a | b |\n|---|---|\n{rows}\n\n" + "\n".join(f"後文{i}です。" for i in range(80)))
        assert "後文0です。" in "".join(pages) and "後文79です。" in pages[-1]
        assert "後文0です。" not in pages[-1]

    def test_tall_first_row_adds_no_blank_page(self):
        assert "長" in self._pages("| a | b |\n|---|---|\n| %s | x |" % ("長" * 4000))[0]

    def test_consecutive_tables(self):
        one = "| a%d | b |\n|---|---|\n| 1 | 2 |"
        text = self._flat("\n\n".join(one % i for i in range(30)))
        assert all(f"a{i}" in text for i in range(30))
