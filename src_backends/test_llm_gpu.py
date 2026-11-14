"""LLM の GPU 実行（Vulkan）: 装置の選択・CPU への切り替え・強制終了の印・設定とステータス API

実際のモデルや GPU は使わず、``llama_cpp.Llama`` を差し替えて検査する。"""
import sys
import types

import pytest

from src_backends import local_llm
from src_backends.config import load_config


class FakeLlama:
    """Llama の代わり。n_gpu_layers の値を記録し、失敗の再現もできる"""
    instances: list = []
    fail_on_gpu = False  # 読み込み（コンストラクタ）で GPU 指定なら例外
    fail_generate_on_gpu = False  # 生成時に GPU のインスタンスなら例外

    def __init__(self, model_path, n_ctx, n_threads, n_gpu_layers, verbose):
        if n_gpu_layers != 0 and FakeLlama.fail_on_gpu:
            raise RuntimeError("vulkan out of memory")
        self.n_gpu_layers = n_gpu_layers
        FakeLlama.instances.append(self)

    def create_chat_completion(self, messages, temperature, max_tokens, stream=False):
        if self.n_gpu_layers != 0 and FakeLlama.fail_generate_on_gpu:
            raise RuntimeError("device lost")
        if stream:
            return iter([{"choices": [{"delta": {"content": "こん"}}]},
                         {"choices": [{"delta": {"content": "にちは"}}]}])
        return {"choices": [{"message": {"content": "こんにちは"}}]}


@pytest.fixture
def llama(monkeypatch, tmp_path):
    """llama_cpp を差し替え、モデルファイルと印のファイルの置き場所を一時フォルダにする"""
    FakeLlama.instances = []
    FakeLlama.fail_on_gpu = False
    FakeLlama.fail_generate_on_gpu = False
    fake = types.ModuleType("llama_cpp")
    fake.Llama = FakeLlama
    fake.llama_supports_gpu_offload = lambda: True
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)
    monkeypatch.setattr(local_llm, "_llama_cpp_available", None)
    monkeypatch.setattr(local_llm, "_llamacpp_client", None)
    monkeypatch.setattr(local_llm, "_marker_owned", False)
    monkeypatch.setattr(local_llm, "_gpu_supported", None)
    monkeypatch.setattr(local_llm, "_device_state", {"device": None, "device_name": "", "reason": ""})
    monkeypatch.setattr(local_llm, "gpu_device_name", lambda: "AMD Radeon 760M Graphics")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    model = tmp_path / "m.gguf"
    model.write_text("x")
    fake.model_path = str(model)
    return fake


def _client(llama, device="auto"):
    return local_llm.LlamaCppClient(llama.model_path, device)


class TestDeviceChoice:
    def test_auto_uses_gpu_with_all_layers(self, llama):
        c = _client(llama, "auto")
        c._load_model()
        assert c.device_used == "gpu"
        assert FakeLlama.instances[-1].n_gpu_layers == -1
        st = local_llm.get_llm_device_status("auto")
        assert st["device"] == "gpu" and st["device_name"] == "AMD Radeon 760M Graphics" and st["reason"] == ""

    def test_gpu_setting_uses_gpu(self, llama):
        c = _client(llama, "gpu")
        c._load_model()
        assert c.device_used == "gpu" and FakeLlama.instances[-1].n_gpu_layers == -1

    def test_cpu_setting_never_uses_gpu_even_if_available(self, llama):
        c = _client(llama, "cpu")
        c._load_model()
        assert c.device_used == "cpu" and FakeLlama.instances[-1].n_gpu_layers == 0
        assert local_llm.get_llm_device_status("cpu")["reason"] == "cpu_selected"
        assert not local_llm.gpu_probe_path().exists()  # GPU で読み込まないので印も作らない

    @pytest.mark.parametrize("device", ["auto", "gpu"])
    def test_no_gpu_offload_support_uses_cpu(self, llama, device):
        llama.llama_supports_gpu_offload = lambda: False
        c = _client(llama, device)
        c._load_model()
        assert c.device_used == "cpu" and FakeLlama.instances[-1].n_gpu_layers == 0
        assert local_llm.get_llm_device_status(device)["reason"] == "no_gpu"

    def test_unknown_device_is_normalized_to_auto(self, llama):
        assert local_llm.normalize_llm_device("tpu") == "auto"
        assert local_llm.normalize_llm_device(None) == "auto"
        assert local_llm.normalize_llm_device(" GPU ") == "gpu"
        assert _client(llama, "xyz").device == "auto"


class TestFallback:
    def test_gpu_load_failure_falls_back_to_cpu_and_clears_marker(self, llama, caplog):
        FakeLlama.fail_on_gpu = True
        c = _client(llama, "auto")
        with caplog.at_level("WARNING", logger="voice2doc.local_llm"):
            c._load_model()
        assert c.device_used == "cpu" and FakeLlama.instances[-1].n_gpu_layers == 0
        assert any("CPU で読み込み直します" in r.message for r in caplog.records)
        assert local_llm.get_llm_device_status("auto")["reason"] == "gpu_load_failed"
        assert not local_llm.gpu_probe_path().exists()

    def test_generation_failure_on_gpu_switches_to_cpu_and_retries(self, llama):
        FakeLlama.fail_generate_on_gpu = True
        c = _client(llama, "auto")
        assert c.generate_report("本文", "a.mp3") == "こんにちは"
        assert c.device_used == "cpu"
        assert not local_llm.gpu_probe_path().exists()

    def test_stream_failure_on_gpu_switches_to_cpu_and_retries(self, llama):
        FakeLlama.fail_generate_on_gpu = True
        chunks = []
        c = _client(llama, "auto")
        out = c.generate_report_stream("本文", "a.mp3", on_chunk=chunks.append)
        assert out == "こんにちは" and c.device_used == "cpu"

    def test_failure_on_cpu_is_raised(self, llama):
        llama.llama_supports_gpu_offload = lambda: False
        FakeLlama.fail_generate_on_gpu = False
        c = _client(llama, "cpu")
        c._load_model()
        c._model.create_chat_completion = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            c.generate_report("本文", "a.mp3")


class TestReviewFixes:
    def test_input_errors_do_not_switch_to_cpu(self, llama):
        c = _client(llama, "auto")
        c._load_model()
        c._model.create_chat_completion = lambda **kw: (_ for _ in ()).throw(ValueError("exceed context window"))
        with pytest.raises(ValueError):
            c.generate_report("本文", "a.mp3")
        assert c.device_used == "gpu"
        with pytest.raises(ValueError):
            c.generate_report_stream("本文", "a.mp3")
        assert c.device_used == "gpu"

    def test_marker_is_cleared_on_the_first_streamed_chunk(self, llama):
        c = _client(llama, "auto")
        seen = []

        def on_chunk(text):
            seen.append(local_llm.gpu_probe_path().exists())

        c.generate_report_stream("本文", "a.mp3", on_chunk=on_chunk)
        assert seen[0] is False  # 最初のチャンクの時点で消えている

    def test_clean_exit_removes_the_own_marker_only(self, llama):
        _client(llama, "auto")._load_model()
        local_llm._cleanup_probe_marker_at_exit()
        assert not local_llm.gpu_probe_path().exists()
        # 前回の強制終了の印（自分が作っていない）は残す
        p = local_llm.gpu_probe_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("loading")
        local_llm._cleanup_probe_marker_at_exit()
        assert p.exists()

    def test_gpu_support_result_is_cached_even_when_it_fails(self, llama):
        calls = []

        def boom():
            calls.append(1)
            raise OSError("vulkan-1.dll missing")

        llama.llama_supports_gpu_offload = boom
        assert local_llm.gpu_offload_supported() is False
        assert local_llm.gpu_offload_supported() is False
        assert len(calls) == 1

    def test_device_is_read_at_each_generation(self, test_client, monkeypatch):
        import src_backends.main as main_mod
        from src_backends.config import update_config
        main_mod.local_llm.get_llamacpp_client.reset_mock()
        update_config(llm_device="cpu")
        monkeypatch.setattr("src_backends.main._reload_llm_for_device", lambda *a: None)
        # 設定を cpu に切り替えた後の生成は cpu で取得する
        assert main_mod.normalize_llm_device(main_mod.load_config().llm_device) == "cpu"


class TestCrashMarker:
    def test_marker_exists_while_loaded_until_first_generation_succeeds(self, llama):
        c = _client(llama, "auto")
        c._load_model()
        assert local_llm.gpu_probe_path().exists()  # 読み込み後〜最初の生成成功までは残る
        assert c.generate_report("本文", "a.mp3") == "こんにちは"
        assert not local_llm.gpu_probe_path().exists()

    def test_marker_is_removed_after_first_stream_succeeds(self, llama):
        c = _client(llama, "auto")
        c.generate_report_stream("本文", "a.mp3")
        assert not local_llm.gpu_probe_path().exists()

    def test_marker_is_created_before_the_gpu_load(self, llama, monkeypatch):
        seen = {}
        orig = FakeLlama.__init__

        def spy(self_, *a, **kw):
            seen["marker_during_load"] = local_llm.gpu_probe_path().exists()
            orig(self_, *a, **kw)

        monkeypatch.setattr(FakeLlama, "__init__", spy)
        _client(llama, "auto")._load_model()
        assert seen["marker_during_load"] is True

    def test_previous_crash_makes_auto_use_cpu(self, llama):
        p = local_llm.gpu_probe_path()
        p.parent.mkdir(parents=True)
        p.write_text("loading")
        c = _client(llama, "auto")
        c._load_model()
        assert c.device_used == "cpu" and FakeLlama.instances[-1].n_gpu_layers == 0
        st = local_llm.get_llm_device_status("auto")
        assert st["reason"] == "previous_crash" and st["gpu_disabled_by_crash"] is True
        assert p.exists()  # 自動のままでは印は残り、次回も CPU

    def test_explicit_gpu_retries_after_crash_and_recovers(self, llama):
        p = local_llm.gpu_probe_path()
        p.parent.mkdir(parents=True)
        p.write_text("loading")
        c = _client(llama, "gpu")
        assert c.generate_report("本文", "a.mp3") == "こんにちは"
        assert c.device_used == "gpu" and not p.exists()
        # 印が消えたので、自動に戻しても GPU を使う
        c2 = _client(llama, "auto")
        c2._load_model()
        assert c2.device_used == "gpu"

    def test_cpu_setting_ignores_marker_state(self, llama):
        p = local_llm.gpu_probe_path()
        p.parent.mkdir(parents=True)
        p.write_text("loading")
        c = _client(llama, "cpu")
        c._load_model()
        assert c.device_used == "cpu" and p.exists()


class TestOwnMarker:
    def test_marker_made_by_this_process_is_not_a_previous_crash(self, llama):
        _client(llama, "gpu")._load_model()  # 読み込み済み・最初の生成はまだ（印が残る）
        assert local_llm.gpu_probe_path().exists()
        assert local_llm.gpu_crash_marker_exists() is False
        c = _client(llama, "auto")
        c._load_model()
        assert c.device_used == "gpu"  # 自分が作った印で CPU にはならない

    def test_switching_to_cpu_removes_the_own_marker(self, llama):
        _client(llama, "gpu")._load_model()
        _client(llama, "cpu")._load_model()
        assert not local_llm.gpu_probe_path().exists()

    def test_preload_warms_up_the_gpu_and_clears_the_marker(self, llama):
        local_llm.preload_model(llama.model_path, "gpu")
        assert local_llm.get_llm_device_status("gpu")["device"] == "gpu"
        assert not local_llm.gpu_probe_path().exists()

    def test_preload_warmup_failure_falls_back_to_cpu(self, llama):
        FakeLlama.fail_generate_on_gpu = True
        local_llm.preload_model(llama.model_path, "auto")
        st = local_llm.get_llm_device_status("auto")
        assert st["device"] == "cpu" and st["reason"] == "gpu_load_failed"
        assert not local_llm.gpu_probe_path().exists()


class TestClientRecreation:
    def test_changing_device_recreates_the_client(self, llama):
        a = local_llm.get_llamacpp_client(llama.model_path, "auto")
        assert local_llm.get_llamacpp_client(llama.model_path, "auto") is a
        b = local_llm.get_llamacpp_client(llama.model_path, "cpu")
        assert b is not a and b.device == "cpu"

    def test_preload_loads_with_the_new_device(self, llama):
        local_llm.preload_model(llama.model_path, "cpu")
        assert local_llm.get_llm_device_status("cpu")["device"] == "cpu"
        local_llm.preload_model(llama.model_path, "gpu")
        assert local_llm.get_llm_device_status("gpu")["device"] == "gpu"

    def test_preload_skips_missing_model(self, llama, tmp_path):
        local_llm.preload_model(str(tmp_path / "none.gguf"), "auto")
        assert local_llm.get_llm_device_status("auto")["device"] is None


class TestConfigAndStatusApi:
    def test_default_is_auto(self, test_client):
        assert load_config().llm_device == "auto"
        assert test_client.get("/config").json()["llm_device"] == "auto"

    @pytest.mark.parametrize("device", ["auto", "gpu", "cpu"])
    def test_valid_values_are_saved(self, test_client, monkeypatch, device):
        monkeypatch.setattr("src_backends.main._reload_llm_for_device", lambda *a: None)
        r = test_client.post("/config", json={"llm_device": device})
        assert r.json()["status"] == "ok"
        assert load_config().llm_device == device
        assert test_client.get("/config").json()["llm_device"] == device

    def test_invalid_value_is_rejected_and_nothing_saved(self, test_client):
        r = test_client.post("/config", json={"llm_device": "tpu", "dark_mode": True})
        assert r.status_code == 400
        assert load_config().llm_device == "auto" and load_config().dark_mode is False

    def test_edited_invalid_value_in_config_is_returned_as_auto(self, test_client):
        from src_backends.config import update_config
        update_config(llm_device="bogus")
        assert test_client.get("/config").json()["llm_device"] == "auto"

    def test_changing_device_reloads_the_model_in_background(self, test_client, monkeypatch):
        calls = []
        monkeypatch.setattr("src_backends.main._reload_llm_for_device", lambda mp, dev: calls.append((mp, dev)))
        test_client.post("/config", json={"llm_device": "cpu"})
        test_client.post("/config", json={"llm_device": "cpu"})  # 変更なし
        assert calls == [(None, "cpu")]

    def test_reset_model_tab_restores_auto(self, test_client, monkeypatch):
        monkeypatch.setattr("src_backends.main._reload_llm_for_device", lambda *a: None)
        test_client.post("/config", json={"llm_device": "cpu"})
        test_client.post("/config/reset", json={"tab": "model"})
        assert load_config().llm_device == "auto"

    def test_llm_status_endpoint(self, test_client):
        import src_backends.main as main_mod
        main_mod.local_llm.get_llm_device_status.return_value = {
            "configured": "auto", "device": "gpu", "device_name": "AMD Radeon 760M Graphics",
            "reason": "", "gpu_available": True, "gpu_disabled_by_crash": False}
        assert test_client.get("/llm/status").json() == {
            "configured": "auto", "device": "gpu", "device_name": "AMD Radeon 760M Graphics",
            "reason": "", "gpu_available": True, "gpu_disabled_by_crash": False}
        main_mod.local_llm.get_llm_device_status.assert_called_with("auto")

    def test_llm_status_before_loading(self, test_client):
        import src_backends.main as main_mod
        main_mod.local_llm.get_llm_device_status.return_value = {
            "configured": "cpu", "device": None, "device_name": "", "reason": "",
            "gpu_available": False, "gpu_disabled_by_crash": False}
        r = test_client.get("/llm/status").json()
        assert r["device"] is None and r["configured"] == "cpu"

    def test_real_status_function_reflects_the_loaded_client(self, llama):
        local_llm.preload_model(llama.model_path, "cpu")
        st = local_llm.get_llm_device_status("cpu")
        assert st["configured"] == "cpu" and st["device"] == "cpu" and st["reason"] == "cpu_selected"
        assert st["gpu_available"] is True and st["gpu_disabled_by_crash"] is False


class TestRequirementsAndWorkflows:
    def test_vulkan_wheel_is_pinned(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        req = (root / "requirements.txt").read_text(encoding="utf-8")
        assert "llama-cpp-python==0.3.36" in req
        assert "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/vulkan" in req
        for wf in ("ci.yml", "release.yml"):
            text = (root / ".github" / "workflows" / wf).read_text(encoding="utf-8")
            assert "whl/cpu" not in text, wf


class TestCpuLibraryFallback:
    """Vulkan ローダー（vulkan-1.dll）の無い PC では、同梱の CPU 版ライブラリへ切り替える（ローダーは再配布しない）"""

    @pytest.fixture
    def frozen(self, monkeypatch, tmp_path):
        (tmp_path / "llama_cpp" / "lib_cpu").mkdir(parents=True)
        monkeypatch.setattr(local_llm, "_lib_prepared", False)
        monkeypatch.setattr(local_llm, "is_frozen", lambda: True)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.delenv("LLAMA_CPP_LIB_PATH", raising=False)
        monkeypatch.delitem(sys.modules, "llama_cpp", raising=False)
        return tmp_path / "llama_cpp" / "lib_cpu"

    def test_no_loader_switches_to_the_cpu_libraries(self, frozen, monkeypatch):
        import os
        monkeypatch.setattr(local_llm, "_vulkan_loader_available", lambda: False)
        local_llm.prepare_llama_cpp_library()
        assert os.environ["LLAMA_CPP_LIB_PATH"] == str(frozen)
        monkeypatch.delenv("LLAMA_CPP_LIB_PATH")  # monkeypatch 経由でなく直接設定されたため後始末

    def test_loader_present_keeps_the_vulkan_libraries(self, frozen, monkeypatch):
        import os
        monkeypatch.setattr(local_llm, "_vulkan_loader_available", lambda: True)
        local_llm.prepare_llama_cpp_library()
        assert "LLAMA_CPP_LIB_PATH" not in os.environ

    def test_user_setting_and_dev_environment_are_left_alone(self, frozen, monkeypatch):
        import os
        monkeypatch.setattr(local_llm, "_vulkan_loader_available", lambda: False)
        monkeypatch.setenv("LLAMA_CPP_LIB_PATH", "X:/mine")
        local_llm.prepare_llama_cpp_library()
        assert os.environ["LLAMA_CPP_LIB_PATH"] == "X:/mine"
        monkeypatch.delenv("LLAMA_CPP_LIB_PATH")
        monkeypatch.setattr(local_llm, "_lib_prepared", False)
        monkeypatch.setattr(local_llm, "is_frozen", lambda: False)  # 開発環境では切り替えない
        local_llm.prepare_llama_cpp_library()
        assert "LLAMA_CPP_LIB_PATH" not in os.environ

    def test_missing_cpu_directory_does_nothing(self, frozen, monkeypatch):
        import os
        frozen.rmdir()
        monkeypatch.setattr(local_llm, "_vulkan_loader_available", lambda: False)
        local_llm.prepare_llama_cpp_library()
        assert "LLAMA_CPP_LIB_PATH" not in os.environ

    def test_build_files_bundle_cpu_libraries_and_not_the_loader(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        spec = (root / "voice2doc.spec").read_text(encoding="utf-8")
        assert "llama_cpp/lib_cpu" in spec and "System32" not in spec
        assert "datas.append((os.path.join(_cpu_lib" in spec
        # アプリ直下に CPU 版が置かれて Vulkan 版を隠さない
        assert "a.binaries = [b for b in a.binaries" in spec and "lib_cpu" in spec
        assert "fetch_cpu_llama_lib.py" in (root / "build.bat").read_text(encoding="utf-8")
        assert "fetch_cpu_llama_lib.py" in (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        assert "vulkan-1" not in (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        script = (root / "scripts" / "fetch_cpu_llama_lib.py").read_text(encoding="utf-8")
        req = (root / "requirements.txt").read_text(encoding="utf-8")
        assert 'VERSION = "0.3.36"' in script and "llama-cpp-python==0.3.36" in req
