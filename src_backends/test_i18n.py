"""表示言語（日本語/英語）の対応のテスト"""
import json
import re
from pathlib import Path

import pytest

from src_backends import i18n, prompt_sets
from src_backends.config import AppConfig
from src_backends.transcript_format import format_transcript, is_japanese

SRC = Path(__file__).resolve().parent.parent / "src"
JS = (SRC / "i18n.js").read_text(encoding="utf-8")
APP_JS = (SRC / "app.js").read_text(encoding="utf-8")
WIZARD_JS = (SRC / "wizard.js").read_text(encoding="utf-8")
INDEX_HTML = (SRC / "index.html").read_text(encoding="utf-8")
WIZARD_HTML = (SRC / "wizard.html").read_text(encoding="utf-8")
JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def _frontend_dictionary() -> dict:
    """src/i18n.js の辞書（JSON形式で書かれている）を取り出す"""
    start = JS.index("const I18N = ") + len("const I18N = ")
    end = JS.index(";\n\n  // ---- 実行時")
    return json.loads(JS[start:end])


FRONT = _frontend_dictionary()


def _placeholders(text: str, pattern: str) -> set:
    return set(re.findall(pattern, text))


class TestFrontendDictionary:
    def test_japanese_and_english_have_the_same_keys(self):
        assert set(FRONT["ja"]) == set(FRONT["en"])

    def test_placeholders_match_between_languages(self):
        for key, ja in FRONT["ja"].items():
            assert _placeholders(ja, r"\{\d+\}") == _placeholders(FRONT["en"][key], r"\{\d+\}"), key

    def test_english_text_has_no_japanese(self):
        offenders = {k: v for k, v in FRONT["en"].items() if JAPANESE.search(v)}
        assert offenders == {}

    def test_html_keys_exist(self):
        for name, html in (("index.html", INDEX_HTML), ("wizard.html", WIZARD_HTML)):
            keys = re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html)
            assert keys, name
            missing = [k for k in keys if k not in FRONT["ja"]]
            assert missing == [], (name, missing)

    def test_t_calls_in_scripts_use_existing_keys(self):
        for name, script in (("app.js", APP_JS), ("wizard.js", WIZARD_JS)):
            keys = set(re.findall(r"\bt\('([a-z0-9_.]+)'", script))
            assert keys, name
            missing = sorted(k for k in keys if k not in FRONT["ja"])
            assert missing == [], (name, missing)

    def test_no_japanese_text_left_in_user_facing_literals_of_app_js(self):
        """app.js の画面に出す文字列は t() 経由（ログ・コメント・既定プロンプト名の判定を除く）"""
        leftovers = []
        for number, line in enumerate(APP_JS.split("\n"), 1):
            code = line.split("//")[0]
            if not JAPANESE.search(code) or "console." in code:
                continue
            leftovers.append((number, line.strip()[:80]))
        assert leftovers == [], leftovers

    def test_title_tags_are_not_translated(self):
        """ウィンドウタイトルは二重起動時の前面化に使うため翻訳しない"""
        assert "<title>話書き - Voice2Doc</title>" in INDEX_HTML


class TestBackendMessages:
    def test_japanese_and_english_have_the_same_keys(self):
        assert set(i18n.MESSAGES["ja"]) == set(i18n.MESSAGES["en"])

    def test_placeholders_match_between_languages(self):
        for key, ja in i18n.MESSAGES["ja"].items():
            assert _placeholders(ja, r"\{[a-z_]+\}") == _placeholders(i18n.MESSAGES["en"][key], r"\{[a-z_]+\}"), key

    def test_english_text_has_no_japanese(self):
        assert {k: v for k, v in i18n.MESSAGES["en"].items() if JAPANESE.search(v)} == {}

    def test_translation_follows_current_language(self):
        i18n.set_language("en")
        assert i18n.t("proc.done") == "Processing is complete"
        i18n.set_language("ja")
        assert i18n.t("proc.done") == "処理が完了しました"

    def test_unknown_key_and_language_fall_back(self):
        assert i18n.t("no.such.key") == "no.such.key"
        assert i18n.tr("fr", "proc.done") == i18n.tr("ja", "proc.done")
        assert i18n.normalize_language(None) == "ja"

    def test_placeholders_are_filled(self):
        assert i18n.tr("en", "file.unsupported", ext=".xyz") == "Unsupported format: .xyz"
        assert i18n.tr("ja", "proc.files_done", n=3) == "3ファイルを処理しました"


class TestEtaIsTranslated:
    def test_remaining_time_in_english(self):
        from src_backends import eta
        i18n.set_language("en")
        assert eta.format_remaining(75) == "1m 15s"
        assert eta.format_remaining(5) == "5s"
        assert eta.format_remaining(3700) == "1h 1m"


class TestPromptSets:
    def test_english_set_has_the_same_structure(self):
        assert set(prompt_sets.PROMPT_SETS["ja"]) == set(prompt_sets.PROMPT_SETS["en"])
        names = prompt_sets.builtin_names("en")
        assert names == ("Proofreading", "Proposal", "Report", "Customer meeting notes")
        assert prompt_sets.builtin_names("ja") == ("文章校正", "企画書", "報告書", "お客様面談記録")
        for entry in prompt_sets.PROMPT_SETS["en"].values():
            assert not JAPANESE.search(entry["content"] + entry["name"] + entry["description"])

    def test_builtin_names_cover_both_languages(self):
        names = prompt_sets.builtin_names()
        assert set(names) == {"文章校正", "企画書", "報告書", "お客様面談記録",
                              "Proofreading", "Proposal", "Report", "Customer meeting notes"}

    def test_daily_report_prompt_is_gone(self):
        """日次の報告用のプロンプトは初期セットに無い"""
        all_text = json.dumps(prompt_sets.PROMPT_SETS, ensure_ascii=False)
        assert "\u65e5\u5831" not in all_text and "Daily " + "report" not in all_text

    def test_new_config_in_english_gets_english_prompts(self):
        config = AppConfig(ui_language="en")
        assert list(config.saved_prompts) == ["Proofreading", "Proposal", "Report", "Customer meeting notes"]
        assert config.active_prompt_name == "Proofreading"
        assert config.prompt == prompt_sets.default_prompt("en")

    def test_japanese_defaults_are_unchanged(self):
        config = AppConfig()
        assert list(config.saved_prompts) == ["文章校正", "企画書", "報告書", "お客様面談記録"]
        assert config.active_prompt_name == "文章校正"
        assert config.prompt == prompt_sets.default_prompt("ja")

    def test_switch_swaps_unedited_prompts_and_keeps_edited_ones(self):
        config = AppConfig()
        config.saved_prompts["企画書"]["content"] += "\n-自分の追記"
        config.saved_prompts["文章校正"]["output_dir"] = "C:\\out"
        prompt_sets.switch_language(config, "en")
        assert config.ui_language == "en"
        assert "Report" in config.saved_prompts and "報告書" not in config.saved_prompts
        assert "Customer meeting notes" in config.saved_prompts and "お客様面談記録" not in config.saved_prompts
        assert "Proofreading" in config.saved_prompts and "文章校正" not in config.saved_prompts
        assert "企画書" in config.saved_prompts and "Proposal" not in config.saved_prompts  # 編集済みは残す
        assert config.saved_prompts["Proofreading"]["output_dir"] == "C:\\out"  # 保存先などの設定は引き継ぐ
        assert config.active_prompt_name == "Proofreading"
        assert config.prompt == prompt_sets.default_prompt("en")

    def test_switch_back_and_forth_round_trips(self):
        config = AppConfig()
        before = json.dumps(config.saved_prompts, sort_keys=True)
        prompt_sets.switch_language(config, "en")
        prompt_sets.switch_language(config, "ja")
        assert json.dumps(config.saved_prompts, sort_keys=True) == before
        assert config.active_prompt_name == "文章校正"

    def test_recognition_language_follows_default_only(self):
        config = AppConfig()
        prompt_sets.switch_language(config, "en")
        assert config.whisper_language == "auto"
        config.whisper_language = "fr"  # ユーザーが選んだ言語は変えない
        prompt_sets.switch_language(config, "ja")
        assert config.whisper_language == "fr"

    @pytest.mark.parametrize("value,expected", [("ja", "ja"), ("en", "en"), ("auto", None), ("", None), (None, None)])
    def test_recognition_language_value(self, value, expected):
        assert prompt_sets.recognition_language(value) == expected


class TestEnglishTranscriptFormat:
    def test_is_japanese(self):
        assert is_japanese("今日は晴れです")
        assert not is_japanese("Today is sunny and warm.")
        assert is_japanese("")

    def test_one_line_is_split_into_sentences(self):
        text = "Hello everyone. Today we talk about the plan! Are you ready? Let's begin"
        assert format_transcript(text).split("\n") == [
            "Hello everyone.", "Today we talk about the plan!", "Are you ready?", "Let's begin."]

    def test_abbreviations_do_not_split(self):
        text = "Please ask Dr. Smith about it. He is at 5 p.m. Then we meet."
        assert format_transcript(text).split("\n") == [
            "Please ask Dr. Smith about it.", "He is at 5 p.m. Then we meet."]

    def test_broken_lines_are_joined(self):
        text = "this is a long\nsentence that wraps.\nNext one."
        assert format_transcript(text) == "this is a long sentence that wraps.\nNext one."

    def test_paragraphs_every_400_chars_and_idempotent(self):
        sentence = "This is a reasonably long sentence used for the paragraph test. "
        text = (sentence * 20).strip()
        once = format_transcript(text)
        assert "\n\n" in once
        assert format_transcript(once) == once

    def test_no_words_are_lost(self):
        text = "First thing first. second thing here and the third thing too"
        assert re.sub(r"\W+", "", format_transcript(text)) == re.sub(r"\W+", "", text)

    def test_japanese_is_still_formatted_as_before(self):
        assert format_transcript("今日は晴れですね明日も晴れです") != "今日は晴れですね明日も晴れです."


class TestLanguageApi:
    def test_get_config_has_language_and_builtin_names(self, test_client):
        data = test_client.get("/config").json()
        assert data["ui_language"] == "ja"
        assert "報告書" in data["builtin_prompt_names"] and "Report" in data["builtin_prompt_names"]

    def test_switching_language_swaps_defaults_and_messages(self, test_client):
        resp = test_client.post("/config", json={"ui_language": "en"})
        assert resp.json()["message"] == "Settings saved"
        data = test_client.get("/config").json()
        assert data["ui_language"] == "en"
        assert data["active_prompt_name"] == "Proofreading"
        assert data["whisper_language"] == "auto"
        assert "Report" in data["saved_prompts"]
        assert test_client.post("/process/cancel").json()["message"] == "Nothing is being processed"

    def test_invalid_language_is_rejected(self, test_client):
        assert test_client.post("/config", json={"ui_language": "fr"}).status_code == 400
        assert test_client.get("/config").json()["ui_language"] == "ja"

    def test_builtin_prompts_cannot_be_deleted_in_either_language(self, test_client):
        test_client.post("/config", json={"ui_language": "en"})
        resp = test_client.delete("/prompts/Proposal").json()
        assert resp["status"] == "error" and "cannot be deleted" in resp["message"]

    def test_wizard_save_applies_language(self, test_client):
        resp = test_client.post("/wizard/save", json={"ui_language": "en", "whisper_language": "auto"})
        assert resp.json()["status"] == "ok"
        data = test_client.get("/config").json()
        assert data["ui_language"] == "en" and data["wizard_completed"] if "wizard_completed" in data else True
        assert test_client.get("/config").json()["active_prompt_name"] == "Proofreading"
        assert test_client.post("/wizard/save", json={"ui_language": "xx"}).status_code == 400

    def test_reset_uses_the_current_language_defaults(self, test_client):
        test_client.post("/config", json={"ui_language": "en"})
        test_client.post("/config/reset", json={"tab": "prompt"})
        data = test_client.get("/config").json()
        assert set(data["saved_prompts"]) == {"Proofreading", "Proposal", "Report", "Customer meeting notes"}
        assert data["active_prompt_name"] == "Proofreading"


class TestSwitchLanguageKeepsUserPrompts:
    def test_same_name_prompt_made_by_the_user_is_not_overwritten(self):
        config = AppConfig()
        config.saved_prompts["Report"] = {"content": "my own", "description": "", "output_dir": "",
                                          "include_transcript": False}
        prompt_sets.switch_language(config, "en")
        assert config.saved_prompts["Report"]["content"] == "my own"
        assert "報告書" in config.saved_prompts  # 入れ替えなかったので元のまま


# ---- 日英対応の点検の対象を、ウィザード・HTML・スプラッシュ・main.py にも広げる ----
ROOT = SRC.parent
MAIN_PY = (ROOT / "main.py").read_text(encoding="utf-8")
# 翻訳しない日本語（言語の選択肢の名前「日本語」・ウィンドウ題名の「話書き」を含む <title>）
_ALLOWED_HTML_JA = {"日本語", "話書き - Voice2Doc", "話書き - 初回設定ウィザード"}


def _code_lines_with_japanese(script: str):
    """コメント・console 出力を除いた、日本語を含むコードの行"""
    script = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), script, flags=re.S)
    for number, line in enumerate(script.split("\n"), 1):
        code = line.split("//")[0]
        if JAPANESE.search(code) and "console." not in code:
            yield number, line.strip()[:80]


def _untranslated_html_text(html_text: str) -> list:
    """data-i18n の付いていない要素の、日本語を含む文字（script/style の中は除く）"""
    from html.parser import HTMLParser

    void = {"br", "img", "input", "meta", "link", "hr", "source", "wbr"}

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.found = [], []

        def handle_starttag(self, tag, attrs):
            names = {k for k, _ in attrs}
            translated = any(k.startswith("data-i18n") for k in names)
            for k, v in attrs:
                if k in ("title", "placeholder", "aria-label", "alt") and v and JAPANESE.search(v) and not translated:
                    self.found.append(v)
            if tag not in void:
                self.stack.append((tag, translated))

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

        def handle_data(self, data):
            text = data.strip()
            if not text or not JAPANESE.search(text) or not self.stack:
                return
            if self.stack[-1][0] in ("script", "style") or any(flag for _, flag in self.stack):
                return
            if text not in _ALLOWED_HTML_JA:
                self.found.append(text)

    parser = Parser()
    parser.feed(html_text)
    return parser.found


class TestTranslationCoverage:
    def test_no_japanese_text_left_in_wizard_js(self):
        assert list(_code_lines_with_japanese(WIZARD_JS)) == []

    @pytest.mark.parametrize("name", ["index.html", "wizard.html"])
    def test_html_text_goes_through_the_dictionary(self, name):
        assert _untranslated_html_text((SRC / name).read_text(encoding="utf-8")) == []

    @pytest.mark.parametrize("name", ["index.html", "wizard.html"])
    def test_early_error_panel_has_both_languages_and_follows_the_saved_language(self, name):
        """i18n.js が読めないときの案内は HTML に日英を直接持ち、アプリで選んだ言語を優先する"""
        html_text = (SRC / name).read_text(encoding="utf-8")
        assert "画面を表示できませんでした" in html_text and "The window could not be displayed" in html_text
        assert "localStorage.getItem('voice2doc.ui_language')" in html_text

    @pytest.mark.parametrize("lang", ["ja", "en"])
    def test_splash_is_in_the_selected_language(self, lang):
        from src_backends import startup
        pages = [startup.build_splash_html(lang),
                 startup.build_splash_html(lang, i18n.tr(lang, "boot.err_timeout"), "C:\\logs")]
        for page in pages:
            assert f'<html lang="{lang}">' in page
            assert bool(JAPANESE.search(page)) is (lang == "ja")

    def test_main_py_messages_use_existing_keys(self):
        """main.py のメッセージボックス・スプラッシュの文言は tr() 経由で、辞書に日英がある"""
        keys = set(re.findall(r"""\btr\([^,]+,\s*["']([a-z_.]+)["']""", MAIN_PY))
        assert {"boot.msgbox_title", "boot.blank_title", "boot.wizard_title"} <= keys
        for key in keys:
            assert key in i18n.MESSAGES["ja"] and key in i18n.MESSAGES["en"], key
        for call in re.findall(r"(?<!def )show_message_box\((.*?)\)\n", MAIN_PY, re.S):
            assert not JAPANESE.search(call), call

    def test_wizard_window_title_follows_the_language(self):
        """ウィザードの題名は表示言語で変わり、二重起動時の前面化はどちらの題名でも見つける"""
        from src_backends import single_instance
        assert single_instance.WIZARD_WINDOW_TITLES == (i18n.tr("ja", "boot.wizard_title"),
                                                        i18n.tr("en", "boot.wizard_title"))
        assert not JAPANESE.search(i18n.tr("en", "boot.wizard_title"))
