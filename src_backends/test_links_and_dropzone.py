"""変更のテスト

- テキスト入力の本文を結果 .md と同じ名前の .txt として残す
- 外部リンクを既定のブラウザで開く /open-url（https の決まったサイトのみ）
- 処理メニューのプロンプトカードへのファイルのドロップ（静的検査）
- 設定の「情報」タブ・ウィザードの Vibe 案内（静的検査）
- Voice2Doc の名前（ビルド設定・スクリプト）
"""
import json
import re
from pathlib import Path

import pytest

from src_backends import main as main_mod

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
WIZARD_HTML = (ROOT / "src" / "wizard.html").read_text(encoding="utf-8")
WIZARD_JS = (ROOT / "src" / "wizard.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "src" / "style.css").read_text(encoding="utf-8")
I18N_JS = (ROOT / "src" / "i18n.js").read_text(encoding="utf-8")


def _js_function(source: str, name: str) -> str:
    """function name(...) { ... } の本体（波かっこの対応で切り出す）"""
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[i:j + 1]
    raise AssertionError(name)


class TestTextInputIsKept:
    @pytest.fixture
    def client(self, test_client, write_config):
        write_config()
        stream = main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream
        stream.return_value = "# 打ち合わせメモ\n本文"
        return test_client

    def test_text_is_saved_as_txt_with_the_same_stem(self, client, isolate_config):
        result = client.post("/process", json={"files": [], "text_input": "入力した本文\n2行目"}).json()["results"][0]
        md, txt = Path(result["result_file"]), Path(result["transcript_file"])
        assert md.parent == txt.parent == isolate_config["output"]
        assert md.stem == txt.stem and md.stem.endswith("-打ち合わせメモ")
        assert txt.suffix == ".txt"
        assert txt.read_text(encoding="utf-8") == "入力した本文\n2行目"
        assert md.read_text(encoding="utf-8") == "# 打ち合わせメモ\n本文\n"

    def test_existing_txt_or_md_keeps_the_pair_numbered_together(self, client, isolate_config):
        r1 = client.post("/process", json={"files": [], "text_input": "一回目"}).json()["results"][0]
        stem = Path(r1["result_file"]).stem
        Path(r1["result_file"]).unlink()  # .txt だけ残っていても、.md を同じ名前にしない
        r2 = client.post("/process", json={"files": [], "text_input": "二回目"}).json()["results"][0]
        assert Path(r2["result_file"]).stem == Path(r2["transcript_file"]).stem
        assert Path(r2["result_file"]).stem != stem
        assert Path(r1["transcript_file"]).read_text(encoding="utf-8") == "一回目"

    def test_txt_is_listed_with_the_md_and_deleted_with_it(self, client):
        result = client.post("/process", json={"files": [], "text_input": "消える本文"}).json()["results"][0]
        md, txt = Path(result["result_file"]), Path(result["transcript_file"])
        items = client.get("/history").json()["items"]
        assert [i["name"] for i in items] == [md.name]  # .txt は別の履歴として増えない
        assert client.post("/history/delete-files", json={"ids": [items[0]["id"]]}).status_code == 200
        assert not md.exists() and not txt.exists()

    def test_llm_error_saves_nothing(self, client, isolate_config):
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.side_effect = RuntimeError("x")
        try:
            result = client.post("/process", json={"files": [], "text_input": "本文"}).json()["results"][0]
        finally:
            main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.side_effect = None
        assert result["status"] == "error"
        assert list(isolate_config["output"].glob("*.txt")) == []


class TestOpenUrl:
    @pytest.fixture
    def opened(self, monkeypatch):
        import webbrowser
        calls = []
        monkeypatch.setattr(webbrowser, "open", lambda url: calls.append(url))
        return calls

    @pytest.mark.parametrize("url", [
        "https://github.com/fukueilab/Voice2Doc",
        "https://www.8asue.com/fukuei/",
        "https://www.youtube.com/@fukueilab",
        "https://github.com/thewh1teagle/vibe/releases",
    ])
    def test_known_https_links_are_opened(self, test_client, opened, url):
        assert test_client.post("/open-url", json={"url": url}).status_code == 200
        assert opened == [url]

    @pytest.mark.parametrize("url", [
        "http://github.com/fukueilab/Voice2Doc",
        "javascript:alert(1)",
        "file:///C:/Windows/System32/calc.exe",
        "https://evil.example.com/",
        "https://github.com.evil.example/",
        "https://user@github.com/",
        "https://github.com:8443/",
        "https://github.com/a b",
        "",
    ])
    def test_other_links_are_refused(self, test_client, opened, url):
        resp = test_client.post("/open-url", json={"url": url})
        assert resp.status_code == 400
        assert opened == []

    def test_refusal_message_is_translated(self, test_client, opened):
        from src_backends import i18n
        i18n.set_language("en")
        resp = test_client.post("/open-url", json={"url": "http://x"})
        assert resp.json()["detail"].startswith("This link cannot be opened")


class TestAudioDurationOffLoop:
    def test_duration_is_read_in_a_worker_thread(self, test_client, write_config, isolate_config, monkeypatch):
        """ffprobe/ffmpeg の起動でイベントループ（/status・中止）を止めない"""
        import asyncio
        write_config()
        seen = []

        def fake_duration(path):
            # イベントループのスレッドで呼ばれていれば get_running_loop() が成功する
            try:
                asyncio.get_running_loop()
                seen.append("on-loop")
            except RuntimeError:
                seen.append("worker")
            return 12.0

        monkeypatch.setattr(main_mod, "get_audio_duration", fake_duration)
        main_mod.whisper_transcribe.return_value = {"text": "本文"}
        (isolate_config["input"] / "a.wav").write_bytes(b"dummy")
        assert test_client.post("/process", json={"files": ["a.wav"]}).status_code == 200
        assert seen == ["worker"]
        assert "asyncio.to_thread(get_audio_duration" in (ROOT / "src_backends" / "main.py").read_text(encoding="utf-8")


class TestPromptCardDrop:
    def test_card_accepts_file_drops_and_reuses_add_files(self):
        body = _js_function(APP_JS, "renderPromptCards")
        for event in ("'dragover'", "'dragleave'", "'drop'"):
            assert f"card.addEventListener({event}" in body, event
        assert "selectPromptCard(name)" in body
        assert "addFiles(files)" in body  # 拡張子・件数・サイズの検査と警告は既存の addFiles のまま
        assert "card.classList.add('dragover')" in body and "card.classList.remove('dragover')" in body
        # 子要素の上での dragleave で強調が点滅しない・処理中やファイル以外のドラッグは無視する
        assert "card.contains(e.relatedTarget)" in body
        assert body.count("isProcessing || !isFileDrag(e)") == 2

    def test_selecting_a_card_switches_to_the_file_view(self):
        body = _js_function(APP_JS, "selectPromptCard")
        assert "showFileInputView(name)" in body and "active_prompt_name" in body
        assert "'Files'" in _js_function(APP_JS, "isFileDrag")

    def test_dragover_style_exists(self):
        assert ".prompt-card.dragover" in STYLE_CSS


class TestAboutTab:
    def test_tab_and_panel_exist(self):
        assert '<button class="tab-btn" data-tab="about" data-i18n="about.tab">' in INDEX_HTML
        assert 'id="tab-about"' in INDEX_HTML and 'id="about-version"' in INDEX_HTML

    @pytest.mark.parametrize("url", [
        "https://github.com/fukueilab/Voice2Doc",
        "https://www.8asue.com/fukuei/",
        "https://www.youtube.com/@fukueilab",
        "https://github.com/thewh1teagle/vibe/releases",
    ])
    def test_links_open_externally(self, url):
        panel = INDEX_HTML[INDEX_HTML.index('id="tab-about"'):INDEX_HTML.index('id="settings-footer"')]
        assert f'<a href="{url}" data-external>' in panel
        assert main_mod._is_allowed_external_url(url)

    def test_panel_shows_license_prereq_and_notice(self):
        panel = INDEX_HTML[INDEX_HTML.index('id="tab-about"'):INDEX_HTML.index('id="settings-footer"')]
        assert "MIT License" in panel
        for key in ("about.version", "about.license", "about.repository", "about.website", "about.youtube",
                    "about.prereq_title", "about.prereq_vibe", "about.notice"):
            assert f'data-i18n="{key}"' in panel, key

    def test_texts_mention_vibe_files_in_both_languages(self):
        dicts = json.loads(re.search(r"const I18N = (\{.*?\n  \});", I18N_JS, re.S).group(1))
        for lang in ("ja", "en"):
            assert "vibe-server.exe" in dicts[lang]["about.prereq_vibe"]
            assert "ffmpeg.exe" in dicts[lang]["about.prereq_vibe"]
        assert "ライセンス" in dicts["ja"]["about.notice"] and "license" in dicts["en"]["about.notice"].lower()

    def test_tab_is_not_part_of_the_save_footer(self):
        savable = re.search(r"const SAVABLE_TABS = \[(.*?)\];", APP_JS).group(1)
        assert "about" not in savable

    def test_version_is_filled_and_links_go_through_open_url(self):
        assert "getElementById('about-version')" in _js_function(APP_JS, "updateVersionDisplay")
        assert "a[data-external]" in APP_JS and "'/open-url'" in APP_JS


class TestWizardVibeGuide:
    def test_guide_with_link_is_shown_only_when_vibe_is_missing(self):
        assert 'id="vibe-guide" style="display:none;"' in WIZARD_HTML
        assert 'href="https://github.com/thewh1teagle/vibe/releases"' in WIZARD_HTML
        assert 'data-i18n="wiz.vibe_open"' in WIZARD_HTML
        assert "getElementById('vibe-guide').style.display = data.vibe ? 'none' : 'block'" in WIZARD_JS
        assert "a[data-external]" in WIZARD_JS and "/open-url" in WIZARD_JS
        assert "getElementById('btn-vibe-recheck').addEventListener('click', function () {" in WIZARD_JS
        assert "runPrereqChecks();" in WIZARD_JS

    def test_ffmpeg_is_no_longer_called_bundled(self):
        dicts = json.loads(re.search(r"const I18N = (\{.*?\n  \});", I18N_JS, re.S).group(1))
        assert dicts["ja"]["wz.m003"] == "利用可能" and dicts["en"]["wz.m003"] == "Available"


class TestRenamedBuild:
    def test_spec_builds_voice2doc_without_bundled_binaries(self):
        spec = (ROOT / "voice2doc.spec").read_text(encoding="utf-8")
        assert spec.count("name='Voice2Doc'") == 2
        assert "'ffmpeg.exe'" not in spec and "'vibe-server.exe'" not in spec
        assert "binaries = []" in spec

    def test_build_scripts_use_new_names_and_stay_ascii(self):
        for name in ("build.bat", "build_installer.bat"):
            raw = (ROOT / name).read_bytes()
            assert raw.isascii(), name
            text = raw.decode("ascii")
            assert "Voice2Doc" in text
        assert "voice2doc.spec" in (ROOT / "build.bat").read_text(encoding="ascii")
        installer = (ROOT / "build_installer.bat").read_text(encoding="ascii")
        assert "installer\\voice2doc_installer.nsi" in installer and "dist\\Voice2Doc-Setup.exe" in installer

    def test_window_title_and_storage(self):
        main_py = (ROOT / "main.py").read_text(encoding="utf-8")
        assert 'MAIN_TITLE = "話書き - Voice2Doc"' in main_py and "title=MAIN_TITLE" in main_py
        assert '".voice2doc"' in (ROOT / "src_backends" / "startup.py").read_text(encoding="utf-8")
        assert "voice2doc.ui_language" in I18N_JS
