"""API endpoint tests for Voice2Doc FastAPI routes."""
import os
import sys
import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _reset_startup_globals():
    """main.pyのモジュールグローバル（_vibe_ready等）はテスト間で共有されるため、
    このファイル内のどのテストが先に実行されても他のテストに影響しないよう前後で退避・復元する。
    （例: /process の音声ファイル処理テストが実際にensure_vibe_ready()を通して_vibe_readyをTrueにし、
    それが後続のlifespanテストに漏れて失敗する、という回帰を防ぐ）"""
    import src_backends.main as main_mod
    saved = {
        "_startup_complete": main_mod._startup_complete,
        "_startup_message": main_mod._startup_message,
        "_vibe_ready": main_mod._vibe_ready,
        "_vibe_startup_task": main_mod._vibe_startup_task,
        "STARTUP_TASKS_TIMEOUT_SEC": main_mod.STARTUP_TASKS_TIMEOUT_SEC,
    }
    yield
    for key, value in saved.items():
        setattr(main_mod, key, value)


class TestHealthEndpoint:
    def test_health_returns_200(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200

    def test_health_fields(self, test_client):
        data = test_client.get("/health").json()
        assert data["status"] == "ok"
        assert "input_dir" in data
        assert "output_dir" in data
        assert "whisper_backend" in data

    def test_health_app_version_matches_config_constant(self, test_client):
        from src_backends.config import APP_VERSION
        data = test_client.get("/health").json()
        assert data["app_version"] == APP_VERSION


class TestStatusEndpoint:
    def test_status_returns_200(self, test_client):
        resp = test_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_processing" in data
        assert "progress" in data


class TestOpenOutputFolder:
    def test_opens_output_dir_on_windows(self, test_client, isolate_config):
        with patch("platform.system", return_value="Windows"), \
             patch("subprocess.Popen") as mock_popen:
            resp = test_client.post("/open-output-folder")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_popen.assert_called_once_with(["explorer", str(isolate_config["output"])])

    def test_returns_error_status_on_failure(self, test_client):
        with patch("platform.system", return_value="Windows"), \
             patch("subprocess.Popen", side_effect=OSError("boom")):
            resp = test_client.post("/open-output-folder")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"


class TestPrereqCheckEndpoint:
    def test_returns_setup_checker_results(self, test_client):
        fake_results = {
            "vc_runtime": True,
            "webview2": False,
            "ffmpeg": True,
            "vibe": True,
            "disk_space": True,
            "gpu": {"has_amd_igpu": False, "has_nvidia": True, "has_intel": False},
        }
        with patch("src_backends.main.setup_checker.run_all_checks", return_value=fake_results):
            resp = test_client.get("/prereq-check")
        assert resp.status_code == 200
        assert resp.json() == fake_results

    def test_returns_500_on_unexpected_error(self, test_client):
        with patch("src_backends.main.setup_checker.run_all_checks", side_effect=RuntimeError("boom")):
            resp = test_client.get("/prereq-check")
        assert resp.status_code == 500


class TestListFiles:
    def test_empty_files(self, test_client):
        data = test_client.get("/files").json()
        assert data["files"] == []

    def test_files_after_upload(self, test_client, isolate_config):
        (isolate_config["input"] / "test.wav").write_bytes(b"fake audio")
        data = test_client.get("/files").json()
        names = [f["name"] for f in data["files"]]
        assert "test.wav" in names


class TestGetConfig:
    def test_get_config(self, test_client):
        resp = test_client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "whisper_model" in data
        assert "allowed_extensions" in data


class TestPostConfig:
    def test_update_config(self, test_client, write_config):
        write_config()
        resp = test_client.post("/config", json={"whisper_model": "small"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestUpload:
    def test_upload_txt(self, test_client, write_config):
        write_config()
        resp = test_client.post(
            "/upload",
            files=[("files", ("test.txt", b"hello world", "text/plain"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "test.txt" in data["uploaded"]

    def test_upload_unsupported_extension(self, test_client, write_config):
        write_config()
        resp = test_client.post(
            "/upload",
            files=[("files", ("test.exe", b"bad", "application/octet-stream"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) > 0


class TestCancelProcessing:
    def test_cancel_when_not_processing(self, test_client):
        resp = test_client.post("/process/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestProcessEndpoint:
    """POST /process の特性化テスト（process_files()を分割したときの振る舞いを固定する）"""

    def _import_main(self):
        from src_backends import main as main_mod
        return main_mod

    def test_text_input_local_llm_success(self, test_client, write_config, isolate_config):
        write_config()
        main_mod = self._import_main()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "生成されたレポート"

        resp = test_client.post("/process", json={"files": [], "text_input": "これはテスト入力です"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["status"] == "ok"
        assert result["report"] == "生成されたレポート"
        assert result["transcript"] == "これはテスト入力です"

        result_file = Path(result["result_file"])
        assert result_file.exists()
        assert result_file.read_text(encoding="utf-8") == "生成されたレポート\n"

    def test_text_input_updates_progress_before_and_after_generation(
        self, test_client, write_config, isolate_config, monkeypatch,
    ):
        """テキスト直接入力パスでも estimated_remaining が更新されること。
        _generate_llm_report の呼び出し前後で _update_progress_and_eta が
        正しい引数で呼ばれることを確認する（表示側の推定時間反映の前提となる配線）。"""
        write_config()
        main_mod = self._import_main()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "生成されたレポート"

        calls = []
        original = main_mod._update_progress_and_eta

        def spy(step_order, step, step_progress, index, total_files, elapsed):
            calls.append((step_order, step, step_progress, index, total_files))
            return original(step_order, step, step_progress, index, total_files, elapsed)

        monkeypatch.setattr(main_mod, "_update_progress_and_eta", spy)

        resp = test_client.post("/process", json={"files": [], "text_input": "これはテスト入力です"})
        assert resp.status_code == 200
        assert calls == [
            (main_mod.PROCESS_STEPS_TEXT, "llm", 0, 0, 1),
            (main_mod.PROCESS_STEPS_TEXT, "llm", 100, 0, 1),
        ]

    def test_file_processing_text_file_skips_whisper(self, test_client, write_config, isolate_config):
        write_config()
        main_mod = self._import_main()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "テキストファイルからの生成結果"

        input_file = isolate_config["input"] / "memo.txt"
        input_file.write_text("これはテキストファイルの内容です", encoding="utf-8")

        resp = test_client.post("/process", json={"files": ["memo.txt"]})
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["status"] == "ok"
        assert result["transcript"] == "これはテキストファイルの内容です"
        main_mod.whisper_transcribe.assert_not_called()

        # 元ファイルはinputに残したまま移動しない。done.logに記録される
        assert input_file.exists()
        assert "memo.txt" in isolate_config["done_log_file"].read_text(encoding="utf-8")

    def test_file_processing_audio_file_calls_whisper(self, test_client, write_config, isolate_config):
        write_config({"whisper_backend": "vibe"})
        main_mod = self._import_main()
        main_mod.whisper_transcribe.return_value = {"text": "音声認識結果テキスト"}
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "音声からの生成結果"

        input_file = isolate_config["input"] / "meeting.wav"
        input_file.write_bytes(b"dummy-audio-bytes")

        resp = test_client.post("/process", json={"files": ["meeting.wav"]})
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["status"] == "ok"
        assert result["transcript"] == "音声認識結果テキスト"
        assert result["report"] == "音声からの生成結果"
        main_mod.whisper_transcribe.assert_called_once()

        assert input_file.exists()
        assert "meeting.wav" in isolate_config["done_log_file"].read_text(encoding="utf-8")

    def test_include_transcript_appends_transcript_section(self, test_client, write_config, isolate_config, sample_config_data):
        config_data = dict(sample_config_data)
        config_data["active_prompt_name"] = "daily"
        config_data["saved_prompts"] = {
            "daily": {
                "content": "Test prompt",
                "description": "desc",
                "output_dir": "",
                "include_transcript": True,
            }
        }
        write_config(config_data)
        main_mod = self._import_main()
        main_mod.whisper_transcribe.return_value = {"text": "文字起こし本文"}
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート本文"

        input_file = isolate_config["input"] / "audio.wav"
        input_file.write_bytes(b"dummy")

        resp = test_client.post("/process", json={"files": ["audio.wav"]})
        data = resp.json()
        result_file = Path(data["results"][0]["result_file"])
        content = result_file.read_text(encoding="utf-8")
        assert "レポート本文" in content
        assert "## 音声認識結果" in content
        assert "文字起こし本文" in content
        # include_transcript=trueでも、.md内への埋め込みに加えて別.txtも常に保存される
        transcript_file = Path(data["results"][0]["transcript_file"])
        assert transcript_file.exists()
        assert transcript_file.read_text(encoding="utf-8") == "文字起こし本文"
        assert transcript_file.stem == result_file.stem

    def test_audio_file_always_saves_sibling_txt_transcript(self, test_client, write_config, isolate_config):
        """include_transcript=falseでも、生の音声認識結果を別.txtとして常に保存すること（項目2）"""
        write_config()
        main_mod = self._import_main()
        main_mod.whisper_transcribe.return_value = {"text": "生の音声認識結果"}
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート"

        input_file = isolate_config["input"] / "meeting.wav"
        input_file.write_bytes(b"dummy-audio-bytes")

        resp = test_client.post("/process", json={"files": ["meeting.wav"]})
        data = resp.json()
        result = data["results"][0]
        result_file = Path(result["result_file"])
        transcript_file = Path(result["transcript_file"])

        assert transcript_file.exists()
        assert transcript_file.read_text(encoding="utf-8") == "生の音声認識結果"
        assert transcript_file.suffix == ".txt"
        # mdに埋め込まれていないこと(include_transcript=false)
        assert "生の音声認識結果" not in result_file.read_text(encoding="utf-8")
        # ペアのstemが一致していること
        assert transcript_file.stem == result_file.stem

    def test_repeated_filename_keeps_md_and_txt_pair_in_sync(self, test_client, write_config, isolate_config):
        """同名ファイルを複数回処理した際、.mdと.txtの連番がペアでずれないこと（項目2）"""
        write_config()
        main_mod = self._import_main()
        main_mod.whisper_transcribe.return_value = {"text": "1回目"}
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート1"

        input_file = isolate_config["input"] / "meeting.wav"
        input_file.write_bytes(b"dummy-1")
        resp1 = test_client.post("/process", json={"files": ["meeting.wav"]})
        result1 = resp1.json()["results"][0]
        assert Path(result1["result_file"]).stem == "meeting"
        assert Path(result1["transcript_file"]).stem == "meeting"

        # 2回目: 同名でもう一度処理（inputに再配置、done.log方式のため元ファイルは消えていない）
        main_mod.whisper_transcribe.return_value = {"text": "2回目"}
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート2"
        resp2 = test_client.post("/process", json={"files": ["meeting.wav"]})
        result2 = resp2.json()["results"][0]
        # .mdが存在するため.txtも含めて連番が1つずれて、両方"meeting_1"になるはず
        assert Path(result2["result_file"]).stem == "meeting_1"
        assert Path(result2["transcript_file"]).stem == "meeting_1"

    def test_uploaded_text_file_does_not_get_spurious_txt_sibling(self, test_client, write_config, isolate_config):
        """アップロードされたファイル自体が.txtの場合、無意味な複製.txtを作らないこと（項目2）"""
        write_config()
        main_mod = self._import_main()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート"

        input_file = isolate_config["input"] / "memo.txt"
        input_file.write_text("ユーザー入力テキスト", encoding="utf-8")

        resp = test_client.post("/process", json={"files": ["memo.txt"]})
        result = resp.json()["results"][0]
        assert result["transcript_file"] is None
        # output_dirに.txtが新規作成されていないことを確認
        assert list(isolate_config["output"].glob("*.txt")) == []

    def test_local_llm_failure_reported_as_error(self, test_client, write_config, isolate_config):
        """クラウドLLMへのフォールバックは廃止済み。ローカルLLM失敗時はエラーとして報告される"""
        write_config()
        main_mod = self._import_main()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.side_effect = RuntimeError("local llm failed")

        resp = test_client.post("/process", json={"files": [], "text_input": "テキスト入力"})
        data = resp.json()
        result = data["results"][0]
        assert result["status"] == "error"

    def test_invalid_filename_rejected(self, test_client, write_config):
        write_config()
        resp = test_client.post("/process", json={"files": [".."]})
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["status"] == "error"
        assert "不正なファイル名" in result["message"]

    def test_missing_file_reported_as_error(self, test_client, write_config):
        write_config()
        resp = test_client.post("/process", json={"files": ["does_not_exist.wav"]})
        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["status"] == "error"
        assert "ファイルが見つかりません" in result["message"]

    def test_partial_failure_continues_to_next_file(self, test_client, write_config, isolate_config):
        write_config()
        main_mod = self._import_main()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "OK生成結果"

        good_file = isolate_config["input"] / "good.txt"
        good_file.write_text("正常なファイル", encoding="utf-8")

        resp = test_client.post("/process", json={"files": ["missing.wav", "good.txt"]})
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["results"][0]["status"] == "error"
        assert data["results"][1]["status"] == "ok"
        assert good_file.exists()

    def test_processed_file_excluded_from_file_list_but_stays_in_input(self, test_client, write_config, isolate_config):
        """doneフォルダへの移動は廃止済み。処理済みファイルはinput/に残るが、
        done.logに記録され、GET /filesの一覧からは除外されること"""
        write_config()
        main_mod = self._import_main()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "生成結果"

        input_file = isolate_config["input"] / "good.txt"
        input_file.write_text("正常なファイル", encoding="utf-8")

        resp = test_client.post("/process", json={"files": ["good.txt"]})
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "ok"

        # 元ファイルはinputに残る
        assert input_file.exists()
        assert "good.txt" in isolate_config["done_log_file"].read_text(encoding="utf-8")

        # が、/filesの一覧には出てこない（処理済みのため）
        files_resp = test_client.get("/files")
        names = [f["name"] for f in files_resp.json()["files"]]
        assert "good.txt" not in names

    def test_process_returns_409_when_already_processing(self, test_client, write_config):
        write_config()
        main_mod = self._import_main()
        main_mod.processing_status["is_processing"] = True
        try:
            resp = test_client.post("/process", json={"files": []})
            assert resp.status_code == 409
        finally:
            main_mod.processing_status["is_processing"] = False


class TestUpdateProgressAndEta:
    """`_update_progress_and_eta()`（進捗率・推定残り時間の計算）の単体テスト。

    重み付きステップリスト(weighted_steps、[(ステップ名, 重み), ...])を受け取り、
    重みに比例して進捗率を計算する（`_build_weighted_steps()`参照）。単一ファイル(total_files=1)の処理でも
    0%に張り付かないこと、重みに応じた進捗率になることの回帰防止テスト。"""

    def _import_main(self):
        from src_backends import main as main_mod
        return main_mod

    @pytest.fixture(autouse=True)
    def _reset_processing_status(self):
        main_mod = self._import_main()
        saved = dict(main_mod.processing_status)
        yield
        main_mod.processing_status.clear()
        main_mod.processing_status.update(saved)

    def test_first_step_start_gives_zero_progress(self):
        main_mod = self._import_main()
        steps = [("ffmpeg", 1.0), ("transcribe", 3.0)]
        main_mod._update_progress_and_eta(steps, "ffmpeg", 0, 0, 1, elapsed=10.0)
        assert main_mod.processing_status["progress"] == 0

    def test_mid_step_gives_weighted_progress(self):
        main_mod = self._import_main()
        # 重み1:3の2ステップ。2番目のステップ(transcribe)開始時点 = 1/4 = 25%
        steps = [("ffmpeg", 1.0), ("transcribe", 3.0)]
        main_mod._update_progress_and_eta(steps, "transcribe", 0, 0, 1, elapsed=10.0)
        assert main_mod.processing_status["progress"] == 25

    def test_progress_calculation_does_not_set_estimate(self):
        """推定時間は進捗率から外挿せず、/status応答時にeta.compute_eta_textで計算する"""
        main_mod = self._import_main()
        steps = [("ffmpeg", 1.0), ("transcribe", 3.0)]
        main_mod.processing_status["estimated_remaining"] = "sentinel"
        main_mod._update_progress_and_eta(steps, "transcribe", 0, 0, 1, elapsed=100.0)
        assert main_mod.processing_status["progress"] == 25
        assert main_mod.processing_status["estimated_remaining"] == "sentinel"

    def test_last_step_complete_reaches_full_progress(self):
        main_mod = self._import_main()
        steps = [("ffmpeg", 1.0), ("transcribe", 3.0)]
        main_mod._update_progress_and_eta(steps, "transcribe", 100, 0, 1, elapsed=20.0)
        assert main_mod.processing_status["progress"] == 100

    def test_multi_file_boundary_uses_file_index(self):
        main_mod = self._import_main()
        steps = [("ffmpeg", 1.0), ("transcribe", 3.0)]
        # 3ファイル中2ファイル目(index=1)の最初のステップ(ffmpeg)開始時点
        main_mod._update_progress_and_eta(steps, "ffmpeg", 0, 1, 3, elapsed=10.0)
        assert main_mod.processing_status["progress"] == 33

    def test_unknown_step_falls_back_to_zero(self):
        main_mod = self._import_main()
        main_mod._update_progress_and_eta(main_mod.PROCESS_STEPS_TEXT, "unknown", 0, 0, 1, elapsed=10.0)
        assert main_mod.processing_status["progress"] == 0

    def test_heavier_step_weight_dominates_progress(self):
        """重みが大きいステップほど、その完了が進捗率に大きく寄与すること"""
        main_mod = self._import_main()
        steps = [("a", 1.0), ("b", 9.0)]
        # 軽いステップ(a, 重み1)が完了した時点では全体の1/10 = 10%
        main_mod._update_progress_and_eta(steps, "a", 100, 0, 1, elapsed=10.0)
        assert main_mod.processing_status["progress"] == 10


class TestSavePrompt:
    def test_save_prompt(self, test_client, write_config):
        write_config()
        resp = test_client.post("/prompts", json={
            "name": "custom_prompt",
            "content": "Custom content",
            "description": "test",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestDeleteFile:
    def test_delete_existing(self, test_client, isolate_config):
        (isolate_config["input"] / "to_delete.txt").write_text("delete me")
        resp = test_client.delete("/files/to_delete.txt")
        assert resp.status_code == 200
        assert not (isolate_config["input"] / "to_delete.txt").exists()

    def test_delete_nonexistent(self, test_client):
        resp = test_client.delete("/files/nonexistent.txt")
        assert resp.status_code == 404


class TestClearFiles:
    def test_clear_all(self, test_client, isolate_config):
        (isolate_config["input"] / "a.wav").write_bytes(b"a")
        (isolate_config["input"] / "b.wav").write_bytes(b"b")
        resp = test_client.delete("/files")
        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 2


class TestDeletePrompt:
    def test_delete_custom_prompt(self, test_client, write_config):
        write_config()
        test_client.post("/prompts", json={"name": "to_delete", "content": "x"})
        resp = test_client.delete("/prompts/to_delete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_delete_builtin_prompt_rejected(self, test_client, write_config):
        write_config()
        resp = test_client.delete("/prompts/報告書")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"


class TestGetHistory:
    def test_empty_history(self, test_client, sample_history_file):
        sample_history_file()
        resp = test_client.get("/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_history_with_files(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "report.md").write_text("# Report\nContent")
        data = test_client.get("/history").json()
        names = [item["name"] for item in data["items"]]
        assert "report.md" in names

    def test_list_does_not_include_report_body(self, test_client, isolate_config, sample_history_file):
        """一覧レスポンスは帯域節約のため本文(report)を含まないこと"""
        sample_history_file()
        (isolate_config["output"] / "report.md").write_text("# タイトル\n本文の内容です", encoding="utf-8")
        data = test_client.get("/history").json()
        item = next(i for i in data["items"] if i["name"] == "report.md")
        assert item["report"] is None
        assert item["h1_title"] == "タイトル"

    def test_search_matches_filename(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "daily-report.md").write_text("# 無関係なタイトル\n本文", encoding="utf-8")
        (isolate_config["output"] / "other.md").write_text("# 別のタイトル\n無関係な本文", encoding="utf-8")
        data = test_client.get("/history", params={"q": "daily"}).json()
        names = [i["name"] for i in data["items"]]
        assert names == ["daily-report.md"]

    def test_search_result_includes_h1_title(self, test_client, isolate_config, sample_history_file):
        """検索結果にもh1_titleが正しく含まれること（h1_titleは本文の1行目に由来するため、
        タイトル中の語で検索できることは本文一致(report_match)で担保される。ここではその上で
        h1_titleフィールド自体が一覧と同様に正しく設定されることを確認する）"""
        sample_history_file()
        (isolate_config["output"] / "a.md").write_text("# 特別な企画書\n本文", encoding="utf-8")
        (isolate_config["output"] / "b.md").write_text("# 別件\n本文", encoding="utf-8")
        data = test_client.get("/history", params={"q": "企画書"}).json()
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "a.md"
        assert data["items"][0]["h1_title"] == "特別な企画書"

    def test_search_matches_body_content(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "a.md").write_text("# タイトル\n特定のキーワードを含む本文", encoding="utf-8")
        (isolate_config["output"] / "b.md").write_text("# タイトル\n無関係な内容", encoding="utf-8")
        data = test_client.get("/history", params={"q": "特定のキーワード"}).json()
        names = [i["name"] for i in data["items"]]
        assert names == ["a.md"]

    def test_search_is_case_insensitive(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "a.md").write_text("# Report\nHello World")
        data = test_client.get("/history", params={"q": "HELLO"}).json()
        names = [i["name"] for i in data["items"]]
        assert names == ["a.md"]

    def test_search_no_match_returns_empty_list(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "a.md").write_text("# タイトル\n本文", encoding="utf-8")
        data = test_client.get("/history", params={"q": "存在しない語"}).json()
        assert data["items"] == []

    def test_search_excludes_hidden_files(self, test_client, isolate_config, sample_history_file):
        """検索(?q=)でも一覧と同様に非表示ファイルは除外されること"""
        (isolate_config["output"] / "hidden.md").write_text("# 検索語を含むタイトル\n本文", encoding="utf-8")
        file_id = f"{isolate_config['output']}::hidden.md"
        sample_history_file({"hidden": [{"id": file_id, "hidden_at": time.time() + 5}]})
        data = test_client.get("/history", params={"q": "検索語"}).json()
        assert data["items"] == []


class TestGetHistoryDetail:
    def test_returns_full_report(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "report.md").write_text("# タイトル\n本文の内容です", encoding="utf-8")
        resp = test_client.get("/history/report.md")
        assert resp.status_code == 200
        data = resp.json()
        assert data["report"] == "# タイトル\n本文の内容です"
        assert data["h1_title"] == "タイトル"

    def test_nonexistent_file_returns_404(self, test_client, sample_history_file):
        sample_history_file()
        resp = test_client.get("/history/nonexistent.md")
        assert resp.status_code == 404

    def test_returns_hidden_file_content(self, test_client, isolate_config, sample_history_file):
        """非表示（hidden）状態は一覧表示にのみ影響し、詳細取得（他のhide/delete/download系
        エンドポイントと同様）ではブロックしないこと"""
        (isolate_config["output"] / "hidden.md").write_text("# 隠しファイル\n本文", encoding="utf-8")
        file_id = f"{isolate_config['output']}::hidden.md"
        sample_history_file({"hidden": [{"id": file_id, "hidden_at": time.time()}]})
        resp = test_client.get("/history/hidden.md")
        assert resp.status_code == 200
        assert resp.json()["report"] == "# 隠しファイル\n本文"

    def test_rejects_path_traversal(self, test_client, isolate_config, sample_history_file):
        """scan_dirsの外にあるファイルの中身が、パストラバーサル的なファイル名指定で
        漏れないこと（sanitize_filenameがbasenameを取るため404になり、内容は返らない）"""
        sample_history_file()
        secret_file = isolate_config["base"] / "secret.md"
        secret_file.write_text("SECRET_CONTENT", encoding="utf-8")
        resp = test_client.get("/history/..%5csecret.md")
        assert resp.status_code == 404
        assert "SECRET_CONTENT" not in resp.text


class TestHideHistory:
    def test_hide_file(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "report.md").write_text("# Report")
        resp = test_client.delete("/history/report.md")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_hide_nonexistent(self, test_client, sample_history_file):
        sample_history_file()
        resp = test_client.delete("/history/nonexistent.md")
        assert resp.status_code == 404


class TestClearHistory:
    def test_clear_all_history(self, test_client, isolate_config, sample_history_file):
        sample_history_file()
        (isolate_config["output"] / "r1.md").write_text("# R1")
        (isolate_config["output"] / "r2.md").write_text("# R2")
        resp = test_client.delete("/history")
        assert resp.status_code == 200
        assert resp.json()["hidden_count"] >= 2


class TestModelInfo:
    def test_model_info(self, test_client):
        resp = test_client.get("/model/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "whisper" in data
        assert "available_models" in data


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """ダウンロード先(~/Downloads)を実環境から隔離する"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class TestDownloadHistory:
    def test_download_file(self, test_client, isolate_config, sample_history_file, fake_home):
        sample_history_file()
        (isolate_config["output"] / "download_me.md").write_text("# Download")
        resp = test_client.post("/history/download_me.md/download")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "downloaded" in data
        # format省略時は従来どおり.mdをそのままコピーする
        assert Path(data["downloaded"]).read_text(encoding="utf-8") == "# Download"

    def _download(self, test_client, isolate_config, fmt, text="# 報告書\n**重要**な点\n- 項目A"):
        (isolate_config["output"] / "report.md").write_text(text, encoding="utf-8")
        return test_client.post(f"/history/report.md/download?format={fmt}")

    def test_download_txt(self, test_client, isolate_config, fake_home):
        resp = self._download(test_client, isolate_config, "txt")
        assert resp.status_code == 200
        dest = Path(resp.json()["downloaded"])
        assert dest.suffix == ".txt" and dest.parent == fake_home / "Downloads"
        assert dest.read_bytes().decode("utf-8-sig") == "報告書\r\n\r\n重要な点\r\n・項目A\r\n"

    def test_download_docx(self, test_client, isolate_config, fake_home):
        from docx import Document
        resp = self._download(test_client, isolate_config, "docx")
        dest = Path(resp.json()["downloaded"])
        assert dest.suffix == ".docx"
        assert Document(str(dest)).paragraphs[0].text == "報告書"

    def test_download_pdf(self, test_client, isolate_config, fake_home):
        resp = self._download(test_client, isolate_config, "pdf")
        dest = Path(resp.json()["downloaded"])
        assert dest.suffix == ".pdf" and dest.read_bytes().startswith(b"%PDF")

    def test_download_md_explicit(self, test_client, isolate_config, fake_home):
        resp = self._download(test_client, isolate_config, "md")
        dest = Path(resp.json()["downloaded"])
        assert dest.suffix == ".md" and dest.read_text(encoding="utf-8").startswith("# 報告書")

    def test_invalid_format_rejected(self, test_client, isolate_config, fake_home):
        resp = self._download(test_client, isolate_config, "exe")
        assert resp.status_code == 400
        assert not (fake_home / "Downloads").exists()

    def test_missing_file_404_for_any_format(self, test_client, isolate_config, fake_home):
        resp = test_client.post("/history/nothing.md/download?format=pdf")
        assert resp.status_code == 404

    def test_unique_numbering_when_same_name_exists(self, test_client, isolate_config, fake_home):
        first = Path(self._download(test_client, isolate_config, "txt").json()["downloaded"])
        second = Path(self._download(test_client, isolate_config, "txt").json()["downloaded"])
        assert first.name == "report_報告書.txt" and second.name == "report_報告書_1.txt"

    def test_font_missing_returns_500_with_message(self, test_client, isolate_config, fake_home, monkeypatch):
        from . import output_formats
        monkeypatch.setattr(output_formats, "_pdf_font_path", lambda: fake_home / "none.ttf")
        resp = self._download(test_client, isolate_config, "pdf")
        assert resp.status_code == 500
        assert "フォント" in resp.json()["detail"]

    def test_download_filename_includes_title(self, test_client, isolate_config, fake_home):
        """ダウンロードファイル名に本文のH1見出し（タイトル）が含まれる"""
        resp = self._download(test_client, isolate_config, "txt")
        dest = Path(resp.json()["downloaded"])
        assert dest.name == "report_報告書.txt"

    def test_download_filename_falls_back_without_title(self, test_client, isolate_config, fake_home):
        """H1/H2/H3見出しが無い場合、従来どおりタイトル無しのファイル名になる"""
        resp = self._download(test_client, isolate_config, "txt", text="見出しの無い本文です")
        dest = Path(resp.json()["downloaded"])
        assert dest.name == "report.txt"

    def test_download_filename_title_unsafe_chars_sanitized(self, test_client, isolate_config, fake_home):
        """タイトルにWindows予約文字が含まれる場合、安全な文字に置換される"""
        resp = self._download(test_client, isolate_config, "txt", text='# a/b:c*d\n本文')
        dest = Path(resp.json()["downloaded"])
        assert dest.name == "report_a_b_c_d.txt"

    def test_download_filename_includes_title_for_all_formats(self, test_client, isolate_config, fake_home):
        """md/docx/pdf形式でもファイル名にタイトルが含まれる"""
        for fmt, suffix in (("md", ".md"), ("docx", ".docx"), ("pdf", ".pdf")):
            (isolate_config["output"] / "report.md").write_text("# 報告書\n本文", encoding="utf-8")
            resp = test_client.post(f"/history/report.md/download?format={fmt}")
            dest = Path(resp.json()["downloaded"])
            assert dest.name.startswith("report_報告書"), f"format={fmt}: {dest.name}"
            assert dest.suffix == suffix


class TestDownloadFormatConfig:
    def test_default_is_txt(self, test_client, isolate_config):
        data = test_client.get("/config").json()
        assert data["download_format"] == "txt"
        assert data["download_formats"] == ["txt", "docx", "pdf", "md"]

    def test_update_and_get(self, test_client, isolate_config):
        resp = test_client.post("/config", json={"download_format": "pdf"})
        assert resp.status_code == 200
        assert test_client.get("/config").json()["download_format"] == "pdf"

    def test_invalid_value_rejected_and_not_saved(self, test_client, isolate_config):
        resp = test_client.post("/config", json={"download_format": "rtf"})
        assert resp.status_code == 400
        assert test_client.get("/config").json()["download_format"] == "txt"

    def test_hand_edited_invalid_value_falls_back_to_txt(self, test_client, isolate_config, write_config):
        write_config({"download_format": "rtf"})
        assert test_client.get("/config").json()["download_format"] == "txt"


class TestConfigResetEndpoint:
    """POST /config/reset のテスト"""

    def test_reset_all(self, test_client, write_config):
        """全設定リセット"""
        write_config()
        resp = test_client.post("/config/reset", json={"tab": "all"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_reset_prompt(self, test_client, write_config):
        """プロンプトタブのリセット"""
        write_config()
        resp = test_client.post("/config/reset", json={"tab": "prompt"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_reset_model(self, test_client, write_config):
        """モデルタブのリセット"""
        write_config()
        resp = test_client.post("/config/reset", json={"tab": "model"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_reset_preserves_wizard_completed(self, test_client, write_config):
        """リセット後もwizard_completedが保持されること"""
        write_config({"wizard_completed": True})
        test_client.post("/config/reset", json={"tab": "all"})
        health = test_client.get("/health").json()
        assert health["wizard_completed"] is True

    def test_reset_default_tab(self, test_client, write_config):
        """tab未指定で全リセット（デフォルト動作）"""
        write_config()
        resp = test_client.post("/config/reset", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_reset_prompt_has_builtin_prompts(self, test_client, write_config):
        """プロンプトリセット後、組込みプロンプトが存在すること"""
        write_config()
        test_client.post("/config/reset", json={"tab": "prompt"})
        data = test_client.get("/config").json()
        prompts = data.get("saved_prompts", {})
        assert list(prompts)[:4] == ["文章校正", "企画書", "報告書", "お客様面談記録"]
        assert data["active_prompt_name"] == "文章校正"


class TestWizardEndpoint:
    """ウィザード関連APIのテスト"""

    def test_wizard_status_returns_200(self, test_client):
        """GET /wizard/status が200を返す"""
        resp = test_client.get("/wizard/status")
        assert resp.status_code == 200

    def test_wizard_status_default_false(self, test_client):
        """ウィザード未完了の場合、wizard_completed=false を返す"""
        data = test_client.get("/wizard/status").json()
        assert data["wizard_completed"] is False

    def test_wizard_status_completed(self, test_client, write_config):
        """ウィザード完了後、wizard_completed=true を返す"""
        write_config({"wizard_completed": True})
        data = test_client.get("/wizard/status").json()
        assert data["wizard_completed"] is True

    def test_wizard_save_sets_completed(self, test_client, write_config):
        """POST /wizard/save でwizard_completed=trueに設定"""
        write_config()
        resp = test_client.post("/wizard/save", json={
            "whisper_backend": "vibe",
            "whisper_model": "turbo",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

        # 設定が保存されているか確認
        config_data = test_client.get("/wizard/status").json()
        assert config_data["wizard_completed"] is True

    def test_wizard_save_sets_config_values(self, test_client, write_config):
        """POST /wizard/save で各設定値が正しく保存される"""
        write_config()
        test_client.post("/wizard/save", json={
            "whisper_backend": "python",
            "whisper_model": "small",
            "whisper_language": "en",
        })

        config_data = test_client.get("/config").json()
        assert config_data["whisper_backend"] == "python"
        assert config_data["whisper_model"] == "small"
        assert config_data["whisper_language"] == "en"

    def test_wizard_save_partial_fields(self, test_client, write_config):
        """POST /wizard/save で一部のフィールドのみ更新"""
        write_config({"whisper_model": "tiny"})
        test_client.post("/wizard/save", json={
            "whisper_language": "en",
        })

        config_data = test_client.get("/config").json()
        # whisper_modelは未指定なので変更されない
        assert config_data["whisper_model"] == "tiny"
        assert config_data["whisper_language"] == "en"

    def test_health_includes_wizard_completed(self, test_client, write_config):
        """GET /health に wizard_completed フィールドが含まれる"""
        write_config()
        data = test_client.get("/health").json()
        assert "wizard_completed" in data
        assert data["wizard_completed"] is False

    def test_health_wizard_completed_true(self, test_client, write_config):
        """ウィザード完了後のhealth応答で wizard_completed=true"""
        write_config({"wizard_completed": True})
        data = test_client.get("/health").json()
        assert data["wizard_completed"] is True


class TestStartupLifespan:
    """lifespan()の起動完了フラグが背景初期化タスクの完了を待つことのテスト
    （main.pyのグローバル状態の退避・復元はファイル冒頭のautouseフィクスチャ_reset_startup_globalsが行う）"""

    def test_startup_complete_waits_for_background_tasks(self, isolate_config, write_config):
        """3つの初期化タスクが終わるまでstartup_completeがFalseのままであること"""
        write_config()

        import threading
        import time as time_mod
        from starlette.testclient import TestClient
        from src_backends.main import app

        gate = threading.Event()

        def slow_ensure_model(*args, **kwargs):
            gate.wait(timeout=5)

        # 既定LLMモデルの準備（必ず走る初期化タスク）をゲートにする
        with patch("src_backends.main.local_llm") as mock_llm, \
             patch("src_backends.main.vibe_client") as mock_vibe:
            mock_llm.ensure_model_downloaded.side_effect = slow_ensure_model
            mock_vibe.is_vibe_running.return_value = False
            mock_vibe.get_vibe_port.return_value = None

            with TestClient(app, base_url="http://127.0.0.1:8008", raise_server_exceptions=False) as client:
                # LLMモデル準備タスクがgateで止まっている間はstartup_completeはFalse
                data = client.get("/health").json()
                assert data["startup_complete"] is False

                gate.set()

                for _ in range(50):
                    data = client.get("/health").json()
                    if data["startup_complete"]:
                        break
                    time_mod.sleep(0.1)
                assert data["startup_complete"] is True

    def test_startup_complete_true_even_if_a_task_raises(self, isolate_config, write_config):
        """初期化タスクの一つが例外を送出しても、他のタスクは待ってstartup_completeがTrueになること"""
        write_config({"whisper_backend": "python"})

        from starlette.testclient import TestClient
        from src_backends.main import app
        import time as time_mod

        def raising_get_model(*args, **kwargs):
            raise RuntimeError("モデルダウンロード失敗（テスト用）")

        with patch("src_backends.main.get_model", side_effect=raising_get_model), \
             patch("src_backends.main.local_llm") as mock_llm, \
             patch("src_backends.main.vibe_client") as mock_vibe:
            mock_llm.ensure_model_downloaded.return_value = None
            mock_vibe.is_vibe_running.return_value = False
            mock_vibe.get_vibe_port.return_value = None

            with TestClient(app, base_url="http://127.0.0.1:8008", raise_server_exceptions=False) as client:
                data = {}
                for _ in range(50):
                    data = client.get("/health").json()
                    if data["startup_complete"]:
                        break
                    time_mod.sleep(0.1)
                assert data["startup_complete"] is True

    def test_startup_complete_true_after_timeout(self, isolate_config, write_config, monkeypatch):
        """初期化タスクがタイムアウト秒数以内に終わらない場合でも、タイムアウト後にstartup_completeがTrueになること。
        かつ、タイムアウトしても未完了タスク（Vibe起動）がキャンセルされず、バックグラウンドで完了できること
        （asyncio.wait_for(gather(...))だとgather全体がキャンセルされ子タスクにも伝播してしまう回帰の防止）"""
        write_config({"whisper_backend": "vibe"})

        import threading
        import time as time_mod
        from starlette.testclient import TestClient
        import src_backends.main as main_mod

        monkeypatch.setattr(main_mod, "STARTUP_TASKS_TIMEOUT_SEC", 0.2)

        never_set_gate = threading.Event()

        def hanging_start_vibe(*args, **kwargs):
            never_set_gate.wait(timeout=10)

        with patch("src_backends.main.local_llm") as mock_llm, \
             patch("src_backends.main.vibe_client") as mock_vibe:
            mock_llm.ensure_model_downloaded.return_value = None
            mock_vibe.is_vibe_running.return_value = False
            mock_vibe.get_vibe_port.return_value = None
            mock_vibe.ensure_model.return_value = None
            mock_vibe.start_vibe.side_effect = hanging_start_vibe

            with TestClient(main_mod.app, base_url="http://127.0.0.1:8008", raise_server_exceptions=False) as client:
                data = {}
                for _ in range(50):
                    data = client.get("/health").json()
                    if data["startup_complete"]:
                        break
                    time_mod.sleep(0.1)
                assert data["startup_complete"] is True

                # タイムアウトが起きても、Vibe起動タスク自体はキャンセルされていないこと
                assert main_mod._vibe_startup_task.cancelled() is False
                assert main_mod._vibe_ready is False

                never_set_gate.set()

                # ゲートを開放すればバックグラウンドでタスクが完了し、_vibe_readyがTrueになること
                for _ in range(50):
                    if main_mod._vibe_ready:
                        break
                    time_mod.sleep(0.1)
                assert main_mod._vibe_ready is True


class _RecordingExecutor:
    """run_in_executor経由でどちらのThreadPoolExecutorが実際に使われたかを記録するテスト用ダブル"""

    def __init__(self):
        self.call_count = 0

    def submit(self, fn, *args, **kwargs):
        import concurrent.futures
        self.call_count += 1
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as e:
            future.set_exception(e)
        return future


class TestExecutorPoolSplit:
    """起動時準備タスクとリクエスト処理タスクでThreadPoolExecutorを分離したことの確認"""

    def test_executor_and_startup_executor_are_distinct(self):
        import src_backends.main as main_mod
        assert main_mod.executor is not main_mod.startup_executor

    async def test_ensure_vibe_ready_fallback_uses_startup_executor(self, isolate_config, write_config, monkeypatch):
        """ensure_vibe_ready()のフォールバック起動パス（再起動なし）がstartup_executorのみを
        正確に2回（do_ensure/do_start）使うこと（executor側は一度も呼ばれないこと）"""
        import src_backends.main as main_mod

        write_config({"whisper_backend": "vibe"})
        monkeypatch.setattr(main_mod, "_vibe_ready", False)
        monkeypatch.setattr(main_mod, "_vibe_startup_task", None)

        startup_recorder = _RecordingExecutor()
        request_recorder = _RecordingExecutor()
        monkeypatch.setattr(main_mod, "startup_executor", startup_recorder)
        monkeypatch.setattr(main_mod, "executor", request_recorder)

        with patch("src_backends.main.vibe_client") as mock_vibe:
            mock_vibe.ensure_model.return_value = True
            mock_vibe.start_vibe.return_value = 12345
            mock_vibe.is_vibe_running.return_value = True  # 再起動分岐に入らない

            await main_mod.ensure_vibe_ready()

        assert startup_recorder.call_count == 2  # find_vibe_binary_dir + do_start
        assert request_recorder.call_count == 0
        assert main_mod._vibe_ready is True

    async def test_ensure_vibe_ready_restart_branch_uses_startup_executor(
        self, isolate_config, write_config, monkeypatch
    ):
        """Vibe起動直後に停止していた場合の再起動分岐（do_restart）もstartup_executorを使うこと"""
        import src_backends.main as main_mod

        write_config({"whisper_backend": "vibe"})
        monkeypatch.setattr(main_mod, "_vibe_ready", False)
        monkeypatch.setattr(main_mod, "_vibe_startup_task", None)

        startup_recorder = _RecordingExecutor()
        request_recorder = _RecordingExecutor()
        monkeypatch.setattr(main_mod, "startup_executor", startup_recorder)
        monkeypatch.setattr(main_mod, "executor", request_recorder)

        with patch("src_backends.main.vibe_client") as mock_vibe:
            mock_vibe.ensure_model.return_value = True
            mock_vibe.start_vibe.return_value = 12345
            mock_vibe.is_vibe_running.return_value = False  # 再起動分岐に入る
            mock_vibe.stop_vibe.return_value = None

            await main_mod.ensure_vibe_ready()

        assert startup_recorder.call_count == 3  # find_vibe_binary_dir + do_start + do_restart
        assert request_recorder.call_count == 0
        assert main_mod._vibe_ready is True

    def test_lifespan_startup_tasks_use_startup_executor_not_request_executor(
        self, isolate_config, write_config, monkeypatch
    ):
        """lifespan()の3つの起動時バックグラウンドタスク（Whisperモデル準備/LLM準備/Vibe起動）が
        いずれもstartup_executorのみを使い、リクエスト処理用のexecutorには一切触れないこと。
        （mainブランチの5箇所をexecutorに戻す変異を入れると本テストが失敗することを確認済み）"""
        import time as time_mod
        from starlette.testclient import TestClient
        import src_backends.main as main_mod

        write_config({"whisper_backend": "vibe"})

        startup_recorder = _RecordingExecutor()
        request_recorder = _RecordingExecutor()
        monkeypatch.setattr(main_mod, "startup_executor", startup_recorder)
        monkeypatch.setattr(main_mod, "executor", request_recorder)

        with patch("src_backends.main.vibe_client") as mock_vibe, \
             patch("src_backends.main.local_llm") as mock_llm, \
             patch("src_backends.main.get_model") as mock_get_model:
            mock_vibe.is_vibe_running.return_value = True
            mock_vibe.ensure_model.return_value = True
            mock_vibe.start_vibe.return_value = 12345
            mock_llm.ensure_model_downloaded.return_value = None
            mock_get_model.return_value = object()

            with TestClient(main_mod.app, base_url="http://127.0.0.1:8008", raise_server_exceptions=False) as client:
                data = {}
                for _ in range(50):
                    data = client.get("/health").json()
                    if data["startup_complete"]:
                        break
                    time_mod.sleep(0.1)
                assert data["startup_complete"] is True

        # Vibe起動(検出+do_start)とLLMモデル準備(ensure_model_downloaded)が
        # いずれもstartup_executor経由で実行されたこと
        # （whisper_backend="vibe"のためdownload_whisper_modelは早期return）
        assert startup_recorder.call_count == 3
        assert request_recorder.call_count == 0


class TestDownloadReviewRegressions:
    def test_md_with_bom_converted_without_literal_hash_or_double_bom(self, test_client, isolate_config, tmp_path, monkeypatch):
        home = tmp_path / "home2"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        (isolate_config["output"] / "bom.md").write_bytes("﻿# 見出し\n本文".encode("utf-8"))
        resp = test_client.post("/history/bom.md/download?format=txt")
        data = Path(resp.json()["downloaded"]).read_bytes()
        assert data.decode("utf-8-sig") == "見出し\r\n\r\n本文\r\n"
        assert data.count(b"\xef\xbb\xbf") == 1

    def test_write_unique_file_never_overwrites(self, tmp_path):
        from src_backends.main import _write_unique_file
        (tmp_path / "r.pdf").write_bytes(b"old")
        path = _write_unique_file(tmp_path, "r", ".pdf", b"new")
        assert path.name == "r_1.pdf"
        assert (tmp_path / "r.pdf").read_bytes() == b"old"

    def test_unexpected_conversion_error_returns_500_with_detail(self, test_client, isolate_config, tmp_path, monkeypatch):
        from . import output_formats
        home = tmp_path / "home3"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr(output_formats, "markdown_to_docx", lambda md: (_ for _ in ()).throw(ValueError("boom")))
        (isolate_config["output"] / "x.md").write_text("本文", encoding="utf-8")
        resp = test_client.post("/history/x.md/download?format=docx")
        assert resp.status_code == 500
        assert "boom" in resp.json()["detail"]
