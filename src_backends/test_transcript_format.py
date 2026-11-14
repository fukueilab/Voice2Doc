"""Unit tests for transcript_format.py (生の文字起こしの句読点・改行)"""
import pytest

from src_backends.transcript_format import PARAGRAPH_MIN_CHARS, format_transcript


class TestSentenceEndings:
    def test_polite_ending_gets_period(self):
        assert format_transcript("今日はよろしくお願いします") == "今日はよろしくお願いします。"

    @pytest.mark.parametrize("line", [
        "確認しました", "そうでした", "できません", "そうでしょう", "ご確認ください",
        "いいですね", "頑張りますよ", "始めましょう", "これが原因である",
    ])
    def test_various_polite_endings(self, line):
        assert format_transcript(line) == line + "。"

    @pytest.mark.parametrize("line", ["よろしいですか", "分かりますか", "どうでしょうか", "できませんか"])
    def test_polite_question_gets_question_mark(self, line):
        assert format_transcript(line) == line + "？"

    @pytest.mark.parametrize("line", ["終わりです。", "本当ですか？", "すごい！", "えっ…", "「はい」", "Hello."])
    def test_existing_terminal_punctuation_untouched(self, line):
        assert format_transcript(line) == line

    def test_continuation_endings_do_not_get_period(self):
        """文の途中で切れた行（接続助詞など）には句点を付けない"""
        assert format_transcript("今日は晴れですが") == "今日は晴れですが"


class TestLineJoining:
    def test_segments_without_sentence_end_are_joined_with_next(self):
        src = "今日は会議の\n内容についてお話しします"
        assert format_transcript(src) == "今日は会議の内容についてお話しします。"

    def test_each_sentence_is_on_its_own_line(self):
        src = "こんにちは。\n今日はよろしくお願いします\n進めますね"
        assert format_transcript(src) == "こんにちは。\n今日はよろしくお願いします。\n進めますね。"

    def test_blank_lines_and_surrounding_whitespace_are_ignored(self):
        src = "\n  はじめます  \n\n　終わりです。　\n"
        assert format_transcript(src) == "はじめます。\n終わりです。"

    def test_ascii_fragments_joined_with_space(self):
        assert format_transcript("this is\nan apple.") == "this is an apple."


class TestParagraphs:
    def test_blank_line_inserted_after_enough_characters(self):
        sentence = "あ" * 40 + "です"  # 42文字
        src = "\n".join([sentence] * 8)
        out = format_transcript(src)
        paragraphs = out.split("\n\n")
        assert len(paragraphs) > 1
        # 段落の最後の文で閾値を超えるので、各段落は閾値以上（最後の段落を除く）
        assert all(len(p.replace("\n", "")) >= PARAGRAPH_MIN_CHARS for p in paragraphs[:-1])
        # 文の途中では空行を入れない
        assert all(line.endswith("。") for line in out.replace("\n\n", "\n").split("\n"))

    def test_short_text_stays_single_paragraph(self):
        assert "\n\n" not in format_transcript("はじめます\n終わります")


class TestRobustness:
    def test_empty_and_whitespace(self):
        assert format_transcript("") == ""
        assert format_transcript("  \n \n") == ""

    def test_none_is_treated_as_empty(self):
        assert format_transcript(None) == ""

    def test_idempotent(self):
        src = "\n".join(["今日は会議の内容を共有します", "まず前提です", "次に進めますね", "よろしいですか"] * 12)
        once = format_transcript(src)
        assert format_transcript(once) == once

    def test_idempotent_with_trailing_fragment(self):
        once = format_transcript("終わりました\nそして次は")
        assert once == "終わりました。\nそして次は"
        assert format_transcript(once) == once

    def test_mixed_japanese_and_english_not_broken(self):
        assert format_transcript("AIのすすめです") == "AIのすすめです。"

    def test_no_characters_lost_except_whitespace_and_added_punctuation(self):
        src = "今日は会議の\n内容についてお話しします\nよろしいですか"
        out = format_transcript(src)
        stripped = out.replace("\n", "").replace("。", "").replace("？", "")
        assert stripped == src.replace("\n", "")


class TestJoinLimit:
    def test_casual_speech_is_not_collapsed_into_one_line(self):
        src = "\n".join(["そうだね", "じゃあ行こう", "それでいいよ", "うん"] * 15)
        out = format_transcript(src)
        assert out.count("\n") >= 1
        assert max(len(line) for line in out.replace("\n\n", "\n").split("\n")) < 100

    def test_join_limit_keeps_idempotency(self):
        src = "\n".join(["そうだね", "じゃあ行こう", "それでいいよ", "うん"] * 15)
        once = format_transcript(src)
        assert format_transcript(once) == once

    def test_ascii_punctuation_followed_by_word_gets_space(self):
        assert format_transcript("Hello,\nworld.") == "Hello, world."


class TestInlineSplitting:
    """vibe-serverは全文を改行なしの1行で返す。行の途中にある文末でも区切る"""

    def test_existing_periods_split_into_lines(self):
        src = "こんにちは。今日は晴れです。明日も晴れですが雨かもしれません"
        expected = "こんにちは。\n今日は晴れです。\n明日も晴れですが雨かもしれません。"
        assert format_transcript(src) == expected

    def test_polite_endings_inside_a_line_get_period_and_newline(self):
        src = "本日はよろしくお願いしますそれでは始めましょうまず現状を整理します"
        assert format_transcript(src) == "本日はよろしくお願いします。\nそれでは始めましょう。\nまず現状を整理します。"

    def test_question_inside_a_line_gets_question_mark(self):
        src = "分かりましたか次に進みます"
        assert format_transcript(src) == "分かりましたか？\n次に進みます。"

    def test_closing_quote_after_period_stays_with_the_sentence(self):
        assert format_transcript("彼は「行きます。」と言いましたそして帰りました") == (
            "彼は「行きます。」と言いました。\nそして帰りました。")

    @pytest.mark.parametrize("src", [
        "晴れですが雨かもしれません",            # 接続: が
        "明日行きますので準備します",            # 接続: ので
        "そうですからお願いします",              # 接続: から
        "行きますし帰りますし",                  # 接続: し
        "ますます増えています",                  # 副詞「ますます」
        "行きますけど帰りません",                # 接続: けど
        "そうですって言いました",                # 引用: って
        "お願いしますように",                    # ように
        "そうですねえと思います",                # ねえ
    ])
    def test_continuations_are_not_split(self, src):
        out = format_transcript(src)
        assert out.count("\n") == 0, out

    def test_no_characters_are_lost(self):
        src = "本日はよろしくお願いしますそれでは始めましょうまず現状を整理しますよろしいですかではいきます"
        out = format_transcript(src)
        stripped = out.replace("\n", "").replace("。", "").replace("？", "")
        assert stripped == src

    def test_inline_split_is_idempotent(self):
        src = ("本日はよろしくお願いしますそれでは始めましょうまず現状を整理します"
               "次に課題を確認しますよろしいですか大丈夫ですそれでは終わります") * 6
        once = format_transcript(src)
        assert format_transcript(once) == once

    def test_realistic_single_line_gets_many_lines(self):
        src = ("今日は事業構想についてお話しします。まず背景ですが、現在の課題を整理しますそのうえで解決策を考えます"
               "最後にスケジュールを確認しましょうよろしくお願いします") * 3
        out = format_transcript(src)
        assert len([line for line in out.split("\n") if line]) >= 10
        assert max(len(line) for line in out.split("\n")) < 60

    def test_english_text_is_untouched(self):
        assert format_transcript("Hello, this is a test.") == "Hello, this is a test."

    def test_tiny_remainder_is_not_split_off(self):
        # 区切った後が1文字だけになる場合は区切らない
        assert format_transcript("そうですあ") == "そうですあ"
