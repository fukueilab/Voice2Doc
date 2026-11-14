"""「処理を中止」が確実に効くことのテスト

音声認識（vibe-serverへの1回のHTTP）とAI生成のモデル読み込み中も中止を確認して止まること、
中止したときは /process が "completed" を返さず、画面に「処理完了」を出さないことを確かめる。"""
import threading
import time
from pathlib import Path

import pytest

from src_backends import main as main_mod


@pytest.fixture(autouse=True)
def _restore_globals():
    """中止の処理が触るグローバル状態を、他のテストへ持ち越さない"""
    saved_ready = main_mod._vibe_ready
    saved_fallback = main_mod.processing_status.get("vibe_fallback_active")
    yield
    main_mod._vibe_ready = saved_ready
    main_mod.processing_status["vibe_fallback_active"] = saved_fallback
    main_mod.processing_status["is_cancelled"] = False
    main_mod.processing_status["is_processing"] = False


def _wait_until(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class _Runner:
    """/process を別スレッドで実行し、結果を受け取る"""

    def __init__(self, client, payload):
        self.result = None
        self.elapsed = None

        def run():
            t = time.time()
            self.result = client.post("/process", json=payload)
            self.elapsed = time.time() - t

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def join(self, timeout=10):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "/process が中止後も終わらない"
        return self.result


@pytest.fixture
def blocking_whisper(test_client, write_config, isolate_config):
    """音声認識が「vibe-serverへの長いHTTP」のようにブロックする状態を作る。stop_vibe() されると解放される"""
    write_config()
    release = threading.Event()
    started = threading.Event()

    def fake_transcribe(*a, **k):
        started.set()
        if not release.wait(timeout=20):
            raise TimeoutError("stop_vibe が呼ばれなかった")
        raise RuntimeError("vibe-serverが停止しました")  # 実機ではhttpxが接続エラーになる

    main_mod.whisper_transcribe.side_effect = fake_transcribe
    # 起動確認(ensure_vibe_ready)が「停止していたので再起動」の経路に入らないよう、稼働中として扱う
    main_mod.vibe_client.is_vibe_running.return_value = True
    main_mod.vibe_client.stop_vibe.side_effect = lambda *a, **k: release.set()
    (isolate_config["input"] / "meeting.wav").write_bytes(b"dummy")
    yield started, release
    release.set()
    main_mod.whisper_transcribe.side_effect = None


class TestCancelDuringRecognition:
    def test_cancel_stops_recognition_and_returns_cancelled(self, test_client, blocking_whisper, isolate_config):
        started, _ = blocking_whisper
        runner = _Runner(test_client, {"files": ["meeting.wav"]})
        assert started.wait(5), "音声認識が始まらない"
        assert test_client.post("/process/cancel").json()["status"] == "ok"

        resp = runner.join()
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["results"] == []
        assert runner.elapsed < 8  # 認識の完了を待たずに止まる
        main_mod.vibe_client.stop_vibe.assert_called()  # vibe-serverを止めて認識を打ち切る
        assert main_mod._vibe_ready is False  # 次の処理で設定どおりに再起動させる
        assert list(isolate_config["output"].glob("*")) == []  # 結果ファイルは作られない

    def test_state_is_reset_after_cancel_so_next_run_works(self, test_client, blocking_whisper):
        started, _ = blocking_whisper
        runner = _Runner(test_client, {"files": ["meeting.wav"]})
        started.wait(5)
        test_client.post("/process/cancel")
        runner.join()
        status = test_client.get("/status").json()
        assert status["is_processing"] is False
        assert main_mod.processing_status["is_cancelled"] is False

        # 次の処理は通常どおり完了する
        main_mod.whisper_transcribe.side_effect = None
        main_mod.whisper_transcribe.return_value = {"text": "認識結果です"}
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート"
        body = test_client.post("/process", json={"files": ["meeting.wav"]}).json()
        assert body["status"] == "completed"
        assert body["results"][0]["status"] == "ok"

    def test_cancel_not_processing_is_harmless(self, test_client):
        body = test_client.post("/process/cancel").json()
        assert body["status"] == "ok" and "処理中ではありません" in body["message"]
        assert main_mod.processing_status["is_cancelled"] is False


class TestCancelDuringGeneration:
    def test_text_input_cancel_does_not_save_partial_text(self, test_client, write_config, isolate_config):
        write_config()
        started = threading.Event()

        def slow_stream(*, is_cancelled=None, on_chunk=None, **k):
            started.set()
            partial = ""
            while not (is_cancelled and is_cancelled()):
                partial += "あ"
                if on_chunk:
                    on_chunk(partial)
                time.sleep(0.02)
            return partial  # 中止されても途中までの文章を返す（保存はされない）

        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.side_effect = slow_stream
        try:
            runner = _Runner(test_client, {"files": [], "text_input": "入力テキスト"})
            assert started.wait(5)
            test_client.post("/process/cancel")
            body = runner.join().json()
        finally:
            main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.side_effect = None

        assert body["status"] == "cancelled"
        assert body["results"] == []
        assert list(isolate_config["output"].glob("*")) == []

    def test_generation_cancel_while_model_is_still_loading(self, test_client, write_config, isolate_config):
        """モデル読み込み中（最初のチャンクが出る前）はコールバックが呼ばれないが、中止の要求で待機を打ち切る"""
        write_config()
        started = threading.Event()
        release = threading.Event()

        def loading(*a, **k):
            started.set()
            release.wait(timeout=20)
            return "遅れて完成した文章"

        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.side_effect = loading
        try:
            runner = _Runner(test_client, {"files": [], "text_input": "入力テキスト"})
            assert started.wait(5)
            test_client.post("/process/cancel")
            body = runner.join().json()
            assert body["status"] == "cancelled"
            assert runner.elapsed < 8
            release.set()
            time.sleep(0.3)  # 取り残されたスレッドが遅れて終わっても、何も保存しない
            assert list(isolate_config["output"].glob("*")) == []
        finally:
            release.set()
            main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.side_effect = None

    def test_stale_generation_thread_stops_after_run_ends(self, test_client, write_config):
        """取り残されたAI生成スレッドは、処理が終わった後（フラグがリセットされた後）も止まる"""
        write_config()
        run_id = main_mod._current_run_id
        main_mod._start_processing_status(1)
        started_id = main_mod._current_run_id
        assert started_id != run_id
        main_mod.processing_status["is_cancelled"] = True
        main_mod._finish_processing_status()
        assert main_mod.processing_status["is_cancelled"] is False
        assert main_mod._current_run_id != started_id  # 前回の実行を識別できる


class TestMultiFile:
    def test_cancel_during_second_file_keeps_first_result(self, test_client, write_config, isolate_config):
        write_config()
        (isolate_config["input"] / "a.wav").write_bytes(b"x")
        (isolate_config["input"] / "b.wav").write_bytes(b"x")
        calls = {"n": 0}
        started_second = threading.Event()
        release = threading.Event()

        def fake_transcribe(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"text": "一つ目です"}
            started_second.set()
            release.wait(timeout=20)
            raise RuntimeError("停止")

        main_mod.whisper_transcribe.side_effect = fake_transcribe
        main_mod.vibe_client.is_vibe_running.return_value = True
        main_mod.vibe_client.stop_vibe.side_effect = lambda *a, **k: release.set()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート"
        try:
            runner = _Runner(test_client, {"files": ["a.wav", "b.wav"]})
            assert started_second.wait(8)
            test_client.post("/process/cancel")
            body = runner.join().json()
        finally:
            release.set()
            main_mod.whisper_transcribe.side_effect = None
        assert body["status"] == "cancelled"
        assert [r["file"] for r in body["results"]] == ["a.wav"]
        assert len(list(Path(isolate_config["output"]).glob("a*.md"))) == 1
        assert list(Path(isolate_config["output"]).glob("b*.md")) == []


class TestCancelAfterFinish:
    def test_late_cancel_does_not_turn_a_finished_run_into_cancelled(self, test_client, write_config, isolate_config):
        write_config()
        (isolate_config["input"] / "m.wav").write_bytes(b"x")
        main_mod.whisper_transcribe.return_value = {"text": "認識結果です"}
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート"
        body = test_client.post("/process", json={"files": ["m.wav"]}).json()
        assert body["status"] == "completed"
        # 完了後に押された中止は何もしない
        assert "処理中ではありません" in test_client.post("/process/cancel").json()["message"]


class TestLingeringAndLlmLock:
    def test_process_is_refused_while_a_cancelled_task_still_runs(self, test_client, write_config):
        import asyncio

        from src_backends import main
        write_config()
        fut = asyncio.new_event_loop().create_future()
        main._lingering.add(fut)
        try:
            resp = test_client.post("/process", json={"files": ["x.wav"]})
            assert resp.status_code == 409
        finally:
            main._lingering.discard(fut)

    def test_stale_run_raises_in_recognition_callback_guard(self):
        import pytest

        from src_backends import main
        with pytest.raises(main._ProcessCancelled):
            main._raise_if_stale(main._current_run_id - 1)

    def test_llm_stream_returns_immediately_when_already_cancelled(self):
        from src_backends import local_llm
        client = local_llm.LlamaCppClient("dummy.gguf")
        assert client.generate_report_stream("t", "f", is_cancelled=lambda: True) == ""
