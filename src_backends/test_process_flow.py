"""処理フロー変更のテスト

- 出力ファイル名（元ファイル名-H1タイトル）
- 文字起こしの句読点・改行整形（.txt / .md埋め込み / LLM入力）
- /status の時間表示（音声認識=推定、AI生成=経過時間）と推定係数の学習
"""
import time
from pathlib import Path

import pytest

from src_backends import main as main_mod


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """ダウンロード先(~/Downloads)を実環境から隔離する"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class TestExtractH1Only:
    def test_h1_is_extracted(self):
        assert main_mod.extract_h1_only("# タイトル\n本文") == "タイトル"

    def test_h2_and_h3_are_not_used(self):
        """H1が無いお客様面談記録などでH2の小見出しがファイル名にならないこと"""
        assert main_mod.extract_h1_only("## 小見出し\n### さらに\n本文") is None

    def test_empty_and_none(self):
        assert main_mod.extract_h1_only("") is None
        assert main_mod.extract_h1_only(None) is None
        assert main_mod.extract_h1_only("# \n本文") is None

    def test_first_h1_wins_and_h2_before_it_is_skipped(self):
        assert main_mod.extract_h1_only("## 前置き\n# 本題\n# 二つ目") == "本題"


class TestBuildOutputStem:
    def test_example_from_requirement(self):
        stem = main_mod.build_output_stem(
            "V2026-10-29-52-30", "# AI活用伴走支援サービス「AIのすすめ」事業構想\n本文")
        assert stem == "V2026-10-29-52-30-AI活用伴走支援サービス「AIのすすめ」事業構想"

    def test_without_h1_returns_original_stem(self):
        assert main_mod.build_output_stem("meeting", "見出しなしの本文") == "meeting"
        assert main_mod.build_output_stem("meeting", "## H2だけ") == "meeting"

    def test_unsafe_characters_are_replaced(self):
        assert main_mod.build_output_stem("a", '# x/y:z*w?') == "a-x_y_z_w_"

    def test_double_dots_never_survive(self):
        """履歴APIのis_safe_filename()が".."を拒否するため、保存名に残してはならない"""
        stem = main_mod.build_output_stem("a", "# A..B...C")
        assert ".." not in stem
        assert main_mod.is_safe_filename(stem + ".md")

    def test_only_unusable_characters_falls_back_to_original(self):
        assert main_mod.build_output_stem("a", "# ...") == "a"

    def test_long_title_is_truncated_to_fit_path_limit(self, tmp_path):
        deep = tmp_path / ("d" * 120)
        title = "あ" * 200
        stem = main_mod.build_output_stem("meeting", f"# {title}", deep)
        full = deep / f"{stem}_99.txt"
        assert len(str(full)) < 260
        assert stem.startswith("meeting-")

    def test_extremely_long_directory_drops_title_but_keeps_original(self, tmp_path):
        deep = tmp_path / ("d" * 240)
        assert main_mod.build_output_stem("meeting", "# タイトル", deep) == "meeting"


class TestProcessSaving:
    @pytest.fixture
    def setup(self, test_client, write_config, isolate_config):
        write_config()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = (
            "# 事業構想メモ\n本文です")
        main_mod.whisper_transcribe.return_value = {"text": "今日はよろしくお願いします\n進めますね"}
        (isolate_config["input"] / "V2026-10-29.wav").write_bytes(b"dummy")
        return test_client

    def test_md_and_txt_are_named_original_dash_h1(self, setup, isolate_config):
        result = setup.post("/process", json={"files": ["V2026-10-29.wav"]}).json()["results"][0]
        md, txt = Path(result["result_file"]), Path(result["transcript_file"])
        assert md.name == "V2026-10-29-事業構想メモ.md"
        assert txt.name == "V2026-10-29-事業構想メモ.txt"
        assert md.parent == isolate_config["output"]

    def test_without_h1_names_stay_as_before(self, setup, isolate_config):
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "H1の無い報告書"
        result = setup.post("/process", json={"files": ["V2026-10-29.wav"]}).json()["results"][0]
        assert Path(result["result_file"]).name == "V2026-10-29.md"
        assert Path(result["transcript_file"]).name == "V2026-10-29.txt"

    def test_duplicate_names_keep_pair_in_sync(self, setup):
        r1 = setup.post("/process", json={"files": ["V2026-10-29.wav"]}).json()["results"][0]
        r2 = setup.post("/process", json={"files": ["V2026-10-29.wav"]}).json()["results"][0]
        assert Path(r1["result_file"]).stem == "V2026-10-29-事業構想メモ"
        assert Path(r2["result_file"]).stem == "V2026-10-29-事業構想メモ_1"
        assert Path(r2["transcript_file"]).stem == "V2026-10-29-事業構想メモ_1"

    def test_saved_md_is_listed_in_history_and_downloadable_without_double_title(self, setup, fake_home):
        r = setup.post("/process", json={"files": ["V2026-10-29.wav"]}).json()["results"][0]
        name = Path(r["result_file"]).name
        items = setup.get("/history").json()["items"]
        assert name in [i["name"] for i in items]
        resp = setup.post(f"/history/{name}/download?format=txt")
        assert resp.status_code == 200
        dest = Path(resp.json()["downloaded"])
        assert dest.name == "V2026-10-29-事業構想メモ.txt"  # タイトルを二重に付けない

    def test_text_input_md_name_includes_h1(self, setup, isolate_config):
        result = setup.post("/process", json={"files": [], "text_input": "入力テキスト"}).json()["results"][0]
        name = Path(result["result_file"]).name
        assert name.endswith("-事業構想メモ.md")
        assert name[:4].isdigit() and "_" in name  # YYYY-MM-DD_HHMM-<H1>.md

    def test_transcript_is_punctuated_in_txt_md_and_llm_input(self, setup, write_config, sample_config_data):
        config_data = dict(sample_config_data)
        config_data["active_prompt_name"] = "daily"
        config_data["saved_prompts"] = {"daily": {
            "content": "Test prompt", "description": "d", "output_dir": "", "include_transcript": True}}
        write_config(config_data)
        r = setup.post("/process", json={"files": ["V2026-10-29.wav"]}).json()["results"][0]
        expected = "今日はよろしくお願いします。\n進めますね。"
        assert Path(r["transcript_file"]).read_text(encoding="utf-8") == expected
        assert expected in Path(r["result_file"]).read_text(encoding="utf-8")
        assert r["transcript"] == expected
        stream = main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream
        assert stream.call_args.kwargs["transcript"] == expected

    def test_python_backend_segments_become_lines(self, setup):
        """Pythonバックエンドは連結済み1行のテキストなので、セグメント単位の改行を材料にする"""
        main_mod.whisper_transcribe.return_value = {
            "text": " 今日は会議の内容についてお話しします よろしいですか",
            "segments": [
                {"start": 0, "end": 2, "text": " 今日は会議の内容についてお話しします"},
                {"start": 2, "end": 4, "text": " よろしいですか"},
            ],
        }
        r = setup.post("/process", json={"files": ["V2026-10-29.wav"]}).json()["results"][0]
        assert r["transcript"] == "今日は会議の内容についてお話しします。\nよろしいですか？"

    def test_uploaded_text_file_is_not_reformatted(self, setup, isolate_config):
        (isolate_config["input"] / "memo.txt").write_text("整形しないでください\nそのままです", encoding="utf-8")
        r = setup.post("/process", json={"files": ["memo.txt"]}).json()["results"][0]
        assert r["transcript"] == "整形しないでください\nそのままです"

    def test_recognition_time_is_recorded_for_learning(self, setup, monkeypatch):
        calls = []
        monkeypatch.setattr(main_mod, "get_audio_duration", lambda p: 120.0)
        monkeypatch.setattr(main_mod.eta, "record_run", lambda *a, **k: calls.append(a))
        setup.post("/process", json={"files": ["V2026-10-29.wav"]})
        assert len(calls) == 1
        backend, elapsed, duration, is_video = calls[0]
        assert backend == "vibe" and duration == 120.0 and is_video is False and elapsed >= 0


class TestStatusTimeDisplay:
    @pytest.fixture(autouse=True)
    def _restore_status(self):
        saved = dict(main_mod.processing_status)
        yield
        main_mod.processing_status.clear()
        main_mod.processing_status.update(saved)

    def test_recognition_step_shows_countdown_that_changes_between_polls(self, test_client):
        now = time.time()
        main_mod.processing_status.update(
            is_processing=True, current_step="transcribe",
            recognition_started_at=now - 10, recognition_predicted_sec=130.0)
        first = test_client.get("/status").json()["estimated_remaining"]
        main_mod.processing_status["recognition_started_at"] = now - 60
        second = test_client.get("/status").json()["estimated_remaining"]
        assert first.startswith("推定: ") and second.startswith("推定: ")
        assert first != second

    def test_llm_step_shows_elapsed_time_not_an_estimate(self, test_client):
        main_mod.processing_status.update(
            is_processing=True, current_step="llm", step_started_at=time.time() - 83,
            estimated_remaining="推定: 9秒")  # 古い凍結値が残っていても使わない
        text = test_client.get("/status").json()["estimated_remaining"]
        assert "経過時間" in text and "推定" not in text

    def test_idle_returns_none(self, test_client):
        main_mod.processing_status.update(is_processing=False, current_step="llm", step_started_at=time.time())
        assert test_client.get("/status").json()["estimated_remaining"] is None


class TestReviewFixes:
    def test_title_ending_with_dot_after_truncation_is_safe(self):
        stem = main_mod.build_output_stem("V2026", "# " + "あ" * 39 + ".いうえお")
        assert not stem.endswith(".")
        assert main_mod.is_safe_filename(stem + ".md")

    def test_text_input_name_respects_path_limit(self, tmp_path, monkeypatch):
        deep = tmp_path / ("d" * 235)
        stem = main_mod.build_output_stem("2026-10-30_1200", "# タイトル", deep)
        assert stem == "2026-10-30_1200"

    def test_text_file_llm_step_shows_elapsed_time(self, test_client, write_config, isolate_config, monkeypatch):
        write_config()
        (isolate_config["input"] / "memo.txt").write_text("メモ", encoding="utf-8")
        seen = {}
        original = main_mod._generate_llm_report

        async def spy(*a, **k):
            seen["step"] = main_mod.processing_status["current_step"]
            seen["started"] = main_mod.processing_status["step_started_at"]
            return "レポート"

        monkeypatch.setattr(main_mod, "_generate_llm_report", spy)
        test_client.post("/process", json={"files": ["memo.txt"]})
        assert seen["step"] == "llm"
        assert seen["started"] is not None
        assert original is not None

    def test_next_file_does_not_inherit_previous_timers(self, test_client, write_config, isolate_config, monkeypatch):
        write_config()
        main_mod.processing_status["step_started_at"] = 1.0
        main_mod.processing_status["recognition_started_at"] = 1.0
        (isolate_config["input"] / "memo.txt").write_text("メモ", encoding="utf-8")
        seen = {}

        async def spy(*a, **k):
            seen["recognition"] = main_mod.processing_status["recognition_started_at"]
            return "レポート"

        monkeypatch.setattr(main_mod, "_generate_llm_report", spy)
        test_client.post("/process", json={"files": ["memo.txt"]})
        assert seen["recognition"] is None

    def test_download_name_dedup_is_precise(self, test_client, isolate_config, fake_home):
        # 元ファイル名がタイトルを部分文字列として含んでいても、タイトルは付く
        (isolate_config["output"] / "会議録音.md").write_text("# 会議\n本文", encoding="utf-8")
        dest = Path(test_client.post("/history/会議録音.md/download?format=txt").json()["downloaded"])
        assert dest.name == "会議録音_会議.txt"
        # 新形式（-タイトル）+ 連番でも二重にならない
        (isolate_config["output"] / "rec-会議_1.md").write_text("# 会議\n本文", encoding="utf-8")
        dest = Path(test_client.post("/history/rec-会議_1.md/download?format=txt").json()["downloaded"])
        assert dest.name == "rec-会議_1.txt"
