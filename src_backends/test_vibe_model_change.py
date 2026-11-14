"""設定で文字起こしモデルを変えたとき、再起動なしで新しいモデルが使われること

vibe-server はモデルを起動時にしか指定できないため、稼働中でもモデルが違えば start_vibe が再起動する。"""
import time

import pytest

from src_backends import vibe_client
from src_backends.config import load_config


@pytest.fixture
def running_vibe(monkeypatch):
    """vibe-server が turbo で稼働中の状態にし、停止と起動の呼び出しを記録する"""
    state = {"running": True, "stopped": 0}
    monkeypatch.setattr(vibe_client, "_vibe_process", object())
    monkeypatch.setattr(vibe_client, "_vibe_port", 5555)
    monkeypatch.setattr(vibe_client, "_vibe_model_name", "large-v3-turbo")
    monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: state["running"])

    def fake_stop():
        state["running"] = False
        state["stopped"] += 1

    monkeypatch.setattr(vibe_client, "stop_vibe", fake_stop)
    # 起動し直しの先（vibe-server.exe の検出）で止める。ここに来たこと自体が「再起動しようとした」証拠
    monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda p="": None)
    return state


class TestStartVibeModelChange:
    def test_same_model_keeps_the_running_server(self, running_vibe):
        assert vibe_client.start_vibe(model_name="large-v3-turbo") == 5555
        assert running_vibe["stopped"] == 0

    def test_ggml_prefix_is_not_a_different_model(self, running_vibe, monkeypatch):
        monkeypatch.setattr(vibe_client, "_vibe_model_name", "ggml-large-v3-turbo")
        assert vibe_client.start_vibe(model_name="large-v3-turbo") == 5555
        assert running_vibe["stopped"] == 0

    def test_different_model_restarts_the_server(self, running_vibe, tmp_path, monkeypatch):
        # 新しいモデルのファイルがある状態にする（実機のモデルに頼らない。CI にはモデルが無い）
        fake_model = tmp_path / "ggml-tiny.bin"
        fake_model.write_text("")
        monkeypatch.setattr(vibe_client, "find_model_file", lambda name, vibe_bin_dir=None: fake_model)
        # 停止のあと、起動し直そうとして vibe-server.exe の検出（差し替え済み）で止まる
        with pytest.raises(RuntimeError):
            vibe_client.start_vibe(model_name="tiny")
        assert running_vibe["stopped"] == 1

    def test_unknown_running_model_is_left_alone(self, running_vibe, monkeypatch):
        monkeypatch.setattr(vibe_client, "_vibe_model_name", None)
        assert vibe_client.start_vibe(model_name="tiny") == 5555
        assert running_vibe["stopped"] == 0

    def test_normalize_model_name(self):
        assert vibe_client._normalize_model_name("ggml-tiny") == "tiny"
        assert vibe_client._normalize_model_name(" large-v3 ") == "large-v3"
        assert vibe_client._normalize_model_name("") == ""


class TestConfigChangeAppliesWithoutRestart:
    @pytest.fixture
    def prepared(self, monkeypatch):
        import src_backends.main as main_mod
        calls = []

        async def fake_ensure():
            calls.append("ensure")

        monkeypatch.setattr(main_mod, "ensure_vibe_ready", fake_ensure)
        monkeypatch.setattr(main_mod, "_vibe_ready", True)
        main_mod.processing_status["is_processing"] = False
        yield main_mod, calls
        main_mod.processing_status["is_processing"] = False

    def test_changing_the_model_resets_readiness_and_prepares_in_background(self, test_client, prepared):
        main_mod, calls = prepared
        r = test_client.post("/config", json={"vibe_model": "small"})
        assert r.json()["status"] == "ok"
        assert load_config().vibe_model == "small"
        assert main_mod._vibe_ready is False
        # 背景タスクが走り、準備（モデルの取得と vibe-server の起動し直し）が始まる
        for _ in range(50):  # 背景タスクは別スレッドのイベントループで走る
            if calls:
                break
            time.sleep(0.02)
        assert calls == ["ensure"]

    def test_same_model_does_nothing(self, test_client, prepared):
        main_mod, calls = prepared
        test_client.post("/config", json={"vibe_model": load_config().vibe_model})
        assert main_mod._vibe_ready is True and calls == []

    def test_during_processing_it_is_applied_from_the_next_run(self, test_client, prepared):
        main_mod, calls = prepared
        main_mod.processing_status["is_processing"] = True
        test_client.post("/config", json={"vibe_model": "base"})
        assert main_mod._vibe_ready is False  # 次の処理の ensure_vibe_ready が起動し直す
        assert calls == []  # 処理中は止めない
