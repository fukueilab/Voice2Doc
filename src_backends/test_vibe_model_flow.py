"""文字起こしモデルの取得と起動の流れ（設定のモデルを確認・ダウンロードし、使えないときの切り替えを分かりやすく案内する）

既定の large-v3-turbo ではなく設定のモデルを確認し、別のモデルを選んだときも取得してから vibe-server を起動する。
モデルを用意できないときに「vibe-server.exe が見つからない」案内を出さないことも確かめる。"""
import asyncio
import re
from types import SimpleNamespace

import pytest

from src_backends import vibe_client
from src_backends.config import AppConfig

BYTES = vibe_client._MODEL_EXPECTED_BYTES


def _make_model(folder, name, complete=True):
    path = folder / f"ggml-{name}.bin"
    # 期待サイズの 98% 未満は未完成（実ファイルを作らずに済むよう、疎なファイルにする）
    size = BYTES[name] if complete else 1024
    with open(path, "wb") as f:
        f.truncate(size)
    return path


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(vibe_client, "model_search_dirs", lambda vibe_bin_dir=None: [tmp_path])
    return tmp_path


class TestFindModelFile:
    def test_complete_file_is_found(self, folder):
        path = _make_model(folder, "tiny")
        assert vibe_client.find_model_file("tiny") == path
        assert vibe_client.find_model_file("ggml-tiny") == path  # 接頭辞付きの名前も同じモデル

    def test_incomplete_or_missing_file_is_not_found(self, folder):
        _make_model(folder, "base", complete=False)
        assert vibe_client.find_model_file("base") is None
        assert vibe_client.find_model_file("small") is None

    def test_partial_download_marker_makes_it_incomplete(self, folder):
        _make_model(folder, "tiny")
        (folder / "ggml-tiny.bin.part").write_text("x")
        assert vibe_client.find_model_file("tiny") is None

    def test_ready_models_follow_the_priority_and_skip_excluded(self, folder):
        for name in ("tiny", "large-v3", "large-v3-turbo"):
            _make_model(folder, name)
        _make_model(folder, "small", complete=False)
        assert vibe_client.ready_models() == ["large-v3-turbo", "large-v3", "tiny"]
        assert vibe_client.ready_models(exclude="large-v3-turbo") == ["large-v3", "tiny"]


class TestStartVibeNeverPassesAnUnknownName:
    def test_missing_model_file_raises_a_clear_error(self, folder, monkeypatch, tmp_path):
        exe = tmp_path / "vibe-server.exe"
        exe.write_text("")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda p="": exe)
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: False)
        popen_calls = []
        monkeypatch.setattr(vibe_client.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
        with pytest.raises(RuntimeError, match="large-v3"):
            vibe_client.start_vibe(model_name="large-v3")
        assert popen_calls == []  # 名前だけを渡して vibe-server を起動しない（読めずに終了するだけ）


class TestEnsureModelDownloaded:
    def test_existing_complete_file_is_not_downloaded_again(self, folder, monkeypatch):
        path = _make_model(folder, "tiny")
        monkeypatch.setattr(vibe_client, "find_vibe_binary_dir", lambda: None)
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **k: pytest.fail("ダウンロードしてはいけない"))
        assert vibe_client.ensure_model_downloaded("tiny") == path

    def test_incomplete_file_is_downloaded_again(self, folder, monkeypatch, tmp_path):
        _make_model(folder, "tiny", complete=False)
        monkeypatch.setattr(vibe_client, "find_vibe_binary_dir", lambda: None)
        monkeypatch.setattr(vibe_client, "get_cache_base_dir", lambda: tmp_path / "cache")

        class Resp:
            headers = {"Content-Length": "4"}
            _data = [b"abcd", b""]

            def read(self, n):
                return self._data.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
        result = vibe_client.ensure_model_downloaded("tiny")
        assert result == tmp_path / "cache" / "vibe" / "ggml-tiny.bin"
        assert result.read_bytes() == b"abcd"


# ---------- main.py 側 ----------

@pytest.fixture
def main_mod(monkeypatch):
    import src_backends.main as m
    monkeypatch.setitem(m.processing_status, "vibe_fallback_active", False)
    monkeypatch.setitem(m.processing_status, "vibe_model_notice", "")
    return m


def _fake_vibe(ready=(), binary=True, start_fail=(), downloaded=None, present=()):
    """vibe_client の代わり。取得済みモデル(present)・ダウンロード・起動の成否を指定できる"""
    started = []
    downloaded = downloaded if downloaded is not None else []

    def find_model_file(name, d=None):
        return "path" if name in present or name in downloaded else None

    def start_vibe(model_name="", binary_path=""):
        if model_name in start_fail:
            raise RuntimeError(f"cannot start {model_name}")
        started.append(model_name)

    def ensure_model_downloaded(name, progress_callback=None):
        downloaded.append(name)

    return SimpleNamespace(
        find_model_file=find_model_file, start_vibe=start_vibe, ensure_model_downloaded=ensure_model_downloaded,
        find_vibe_binary_dir=lambda: "dir" if binary else None,
        find_vibe_binary=lambda p="": "exe" if binary else None,
        ready_models=lambda d=None, exclude="": [m for m in ready if m != exclude],
        started=started, downloaded=downloaded,
    )


def _run(coro):
    return asyncio.run(coro)


class TestNoAutomaticDownload:
    """処理時・起動時にはモデルをダウンロードしない。ダウンロードは設定で利用者が選んだときだけ"""

    def test_missing_configured_model_falls_back_to_a_downloaded_one_without_downloading(self, main_mod, monkeypatch):
        fake = _fake_vibe(ready=("large-v3-turbo",), start_fail=("large-v3",))
        monkeypatch.setattr(main_mod, "vibe_client", fake)

        async def go():
            await main_mod._start_vibe_server(AppConfig(vibe_model="large-v3"), asyncio.get_running_loop())

        _run(go())
        assert fake.downloaded == []  # ダウンロードしない
        assert fake.started == ["large-v3-turbo"]
        notice = main_mod.processing_status["vibe_model_notice"]
        assert "large-v3" in notice and "ダウンロード" in notice

    def test_configured_model_present_is_just_started(self, main_mod, monkeypatch):
        fake = _fake_vibe(present=("medium",))
        monkeypatch.setattr(main_mod, "vibe_client", fake)

        async def go():
            await main_mod._start_vibe_server(AppConfig(vibe_model="medium"), asyncio.get_running_loop())

        _run(go())
        assert fake.started == ["medium"] and fake.downloaded == []
        assert main_mod.processing_status["vibe_model_notice"] == ""

    def test_no_model_at_all_raises_for_the_python_fallback_without_downloading(self, main_mod, monkeypatch):
        fake = _fake_vibe(ready=(), start_fail=("large-v3-turbo",))
        monkeypatch.setattr(main_mod, "vibe_client", fake)

        async def go():
            await main_mod._start_vibe_server(AppConfig(vibe_model="large-v3-turbo"), asyncio.get_running_loop())

        with pytest.raises(RuntimeError):
            _run(go())
        assert fake.downloaded == []

    def test_explicit_download_downloads_the_model_and_applies_it(self, main_mod, monkeypatch):
        fake = _fake_vibe()
        monkeypatch.setattr(main_mod, "vibe_client", fake)
        applied = []
        monkeypatch.setattr(main_mod, "_apply_vibe_model_change", lambda: applied.append(1))
        monkeypatch.setattr(main_mod, "load_config", lambda: AppConfig(vibe_model="ggml-large-v3"))
        _run(main_mod._download_vibe_model("large-v3"))
        assert fake.downloaded == ["large-v3"] and applied == [1]
        assert main_mod.processing_status["is_downloading"] is False

    def test_download_of_a_model_that_is_not_the_configured_one_does_not_restart(self, main_mod, monkeypatch):
        fake = _fake_vibe()
        monkeypatch.setattr(main_mod, "vibe_client", fake)
        applied = []
        monkeypatch.setattr(main_mod, "_apply_vibe_model_change", lambda: applied.append(1))
        monkeypatch.setattr(main_mod, "load_config", lambda: AppConfig(vibe_model="small"))
        _run(main_mod._download_vibe_model("medium"))
        assert fake.downloaded == ["medium"] and applied == []

    def test_download_failure_is_reported_in_the_notice(self, main_mod, monkeypatch):
        fake = _fake_vibe()

        def boom(name, progress_callback=None):
            raise RuntimeError("offline")

        fake.ensure_model_downloaded = boom
        monkeypatch.setattr(main_mod, "vibe_client", fake)
        _run(main_mod._download_vibe_model("large-v3"))
        assert "large-v3" in main_mod.processing_status["vibe_model_notice"]
        assert main_mod.processing_status["is_downloading"] is False

    def test_already_downloaded_model_is_not_downloaded_again(self, main_mod, monkeypatch):
        fake = _fake_vibe(present=("small",))
        monkeypatch.setattr(main_mod, "vibe_client", fake)
        _run(main_mod._download_vibe_model("small"))
        assert fake.downloaded == []


class TestModelEndpoints:
    def test_status_endpoint(self, test_client):
        import src_backends.main as m
        m.vibe_client.model_status.return_value = "missing"
        m.vibe_client._MODEL_EXPECTED_BYTES = vibe_client._MODEL_EXPECTED_BYTES
        body = test_client.get("/vibe/model/status", params={"model": "ggml-large-v3"}).json()
        assert body["model"] == "large-v3" and body["status"] == "missing"
        assert body["app_downloading"] is False and body["size_mb"] == round(BYTES["large-v3"] / 1_000_000)

    def test_status_rejects_unsafe_names(self, test_client):
        assert test_client.get("/vibe/model/status", params={"model": "../../x"}).status_code == 400

    def test_download_endpoint_starts_the_download_only_when_not_ready(self, test_client, monkeypatch):
        import src_backends.main as m
        m.vibe_client._MODEL_EXPECTED_BYTES = vibe_client._MODEL_EXPECTED_BYTES
        started = []
        monkeypatch.setattr(m, "_start_vibe_model_download", lambda model: started.append(model))
        m.vibe_client.model_status.return_value = "missing"
        body = test_client.post("/vibe/model/download", json={"model": "small"}).json()
        assert started == ["small"] and body["model"] == "small"
        m.vibe_client.model_status.return_value = "ready"
        test_client.post("/vibe/model/download", json={"model": "tiny"})
        assert started == ["small"]  # 取得済みなら始めない

    @pytest.mark.parametrize("model", ["../../x", "unknown-model", ""])
    def test_download_endpoint_rejects_unknown_or_unsafe_names(self, test_client, model):
        import src_backends.main as m
        m.vibe_client._MODEL_EXPECTED_BYTES = vibe_client._MODEL_EXPECTED_BYTES
        assert test_client.post("/vibe/model/download", json={"model": model}).status_code == 400

    def test_saving_a_model_never_downloads_by_itself(self, test_client, monkeypatch):
        import src_backends.main as m
        started = []
        monkeypatch.setattr(m, "_start_vibe_model_download", lambda model: started.append(model))
        monkeypatch.setattr(m, "_apply_vibe_model_change", lambda: None)
        test_client.post("/config", json={"vibe_model": "large-v3"})
        assert started == []


class TestFrontendFlow:
    def _read(self, rel):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8-sig")

    def test_dialog_has_download_and_cancel_buttons_with_the_required_text(self):
        html = self._read("src/index.html")
        assert 'id="vibe-model-download-modal"' in html
        assert 'id="btn-vibe-dl-download"' in html and 'id="btn-vibe-dl-cancel"' in html
        js = self._read("src/i18n.js")
        assert "音声認識モデルをダウンロードします。ダウンロードに時間が必要です。" in js
        assert '"idx.vibe_dl_download": "ダウンロード"' in js and '"idx.vibe_dl_download": "Download"' in js

    def test_save_asks_before_saving_a_changed_model_and_cancel_aborts(self):
        app = self._read("src/app.js")
        assert "if (!(await confirmVibeModelDownload(transcriptionModel, changed))) return false;" in app
        # 変えていないモデルが未取得のときも確認でき、キャンセルしても他の設定の保存は続ける
        assert "return !changed;" in app
        assert "/vibe/model/download" in app and "/vibe/model/status" in app

    def test_download_progress_is_shown_in_the_banner_and_started_message_is_used(self):
        app = self._read("src/app.js")
        assert "watchVibeModelDownload()" in app and "status.vibe_model_downloading" in app
        assert "app.vibe_dl_started" in app
        js = self._read("src/i18n.js")
        assert js.count('"app.vibe_dl_started"') == 2

    def test_process_menu_shows_the_current_models(self):
        html = self._read("src/index.html")
        assert 'id="current-models"' in html
        app = self._read("src/app.js")
        assert "updateCurrentModels" in app and "app.current_models" in app
        js = self._read("src/i18n.js")
        assert js.count('"app.current_models"') == 2


class TestPythonFallbackMessage:
    def test_vibe_present_gives_a_model_notice_not_the_missing_banner(self, main_mod, monkeypatch):
        monkeypatch.setattr(main_mod, "vibe_client", _fake_vibe(binary=True))
        main_mod._fall_back_to_python(RuntimeError("x"), AppConfig(vibe_model="large-v3"))
        st = main_mod.processing_status
        assert st["vibe_fallback_active"] is True and "large-v3" in st["vibe_model_notice"]

    def test_vibe_missing_keeps_the_original_banner(self, main_mod, monkeypatch):
        monkeypatch.setattr(main_mod, "vibe_client", _fake_vibe(binary=False))
        main_mod._fall_back_to_python(RuntimeError("x"), AppConfig(vibe_model="large-v3"))
        st = main_mod.processing_status
        assert st["vibe_fallback_active"] is True and st["vibe_model_notice"] == ""

    def test_status_endpoint_returns_the_notice(self, test_client):
        import src_backends.main as m
        m.processing_status["vibe_model_notice"] = "お知らせ"
        try:
            assert test_client.get("/status").json()["vibe_model_notice"] == "お知らせ"
        finally:
            m.processing_status["vibe_model_notice"] = ""


class TestFrontend:
    def test_banner_shows_the_notice_and_the_missing_dialog_is_skipped(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "src" / "app.js").read_text(encoding="utf-8")
        assert "status.vibe_model_notice" in js
        assert "st.vibe_model_notice" in js


class TestReviewFindings:
    def test_model_change_during_a_preparation_is_not_lost(self, main_mod, monkeypatch):
        """準備の最中にモデルを変えても、古い設定で始めた準備の終了で「準備済み」に戻らない"""
        monkeypatch.setattr(main_mod, "_vibe_ready", False)
        monkeypatch.setitem(main_mod.processing_status, "is_processing", True)  # 処理中は世代を進めて印を戻すだけ
        started_with = main_mod._vibe_generation  # 準備を始めた時点の世代
        main_mod._apply_vibe_model_change()
        main_mod._mark_vibe_ready(started_with)  # 古い準備の終了
        assert main_mod._vibe_ready is False
        main_mod._mark_vibe_ready(main_mod._vibe_generation)  # 新しい設定での準備の終了
        assert main_mod._vibe_ready is True

    def test_python_fallback_of_an_outdated_preparation_does_not_mark_ready(self, main_mod, monkeypatch):
        monkeypatch.setattr(main_mod, "vibe_client", _fake_vibe(binary=False))
        monkeypatch.setattr(main_mod, "_vibe_ready", False)
        old = main_mod._vibe_generation
        monkeypatch.setattr(main_mod, "_vibe_generation", old + 1)
        main_mod._fall_back_to_python(RuntimeError("x"), AppConfig(), old)
        assert main_mod._vibe_ready is False

    def test_no_download_when_vibe_server_is_not_installed(self, main_mod, monkeypatch):
        fake = _fake_vibe(binary=False)
        monkeypatch.setattr(main_mod, "vibe_client", fake)

        async def go():
            await main_mod._start_vibe_server(AppConfig(vibe_model="large-v3"), asyncio.get_running_loop())

        with pytest.raises(RuntimeError):
            _run(go())
        assert fake.downloaded == [] and fake.started == []

    def test_restart_after_a_crash_uses_the_remembered_model(self, monkeypatch):
        calls = []
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: False)
        monkeypatch.setattr(vibe_client, "_vibe_model_name", "small")
        monkeypatch.setattr(vibe_client, "stop_vibe", lambda: None)

        def fake_start(model_name="", binary_path=""):
            calls.append(model_name)
            raise RuntimeError("stop here")

        monkeypatch.setattr(vibe_client, "start_vibe", fake_start)
        monkeypatch.setattr(vibe_client, "configured_model_name", lambda: "medium")
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            with pytest.raises(RuntimeError):
                vibe_client.transcribe(f.name)
        assert calls == ["small"]  # 設定の "medium" でも既定の turbo でもなく、動いていたモデル

    def test_reset_applies_the_model_change(self, test_client, monkeypatch):
        import src_backends.main as m
        calls = []
        monkeypatch.setattr(m, "_apply_vibe_model_change", lambda: calls.append(1))
        test_client.post("/config", json={"vibe_model": "small"})
        assert calls == [1]
        test_client.post("/config/reset", json={"tab": "model"})
        assert calls == [1, 1]  # 既定（turbo）へ戻るので反映する

    def test_activate_keeps_the_notice_while_another_model_runs(self, test_client, monkeypatch, tmp_path):
        import src_backends.main as m
        mock = m.vibe_client  # test_client が差し替えたモック
        exe = tmp_path / "vibe-server.exe"
        exe.write_text("")
        mock.detect_vibe_binary.return_value = (exe, [str(exe)])
        mock.is_vibe_running.return_value = True
        mock.running_model_name.return_value = "large-v3-turbo"
        monkeypatch.setattr(m, "load_config", lambda: AppConfig(vibe_model="small"))
        monkeypatch.setitem(m.processing_status, "vibe_model_notice", "別のモデルで動作中")
        body = test_client.post("/vibe/activate").json()
        assert body["status"] == "ok"
        assert m.processing_status["vibe_model_notice"] == "別のモデルで動作中"
        mock.running_model_name.return_value = "ggml-small"  # 設定と同じモデル（接頭辞つき）なら案内を消す
        test_client.post("/vibe/activate")
        assert m.processing_status["vibe_model_notice"] == ""


class TestModelNameSafety:
    @pytest.mark.parametrize("name", ["../../x", "..", "a/b", "a\b", "", " ", "x" * 0, "-bad", "a..b"])
    def test_unsafe_names_are_rejected(self, name):
        from src_backends.config import is_valid_vibe_model
        assert is_valid_vibe_model(name) is False

    @pytest.mark.parametrize("name", ["tiny", "large-v3-turbo", "ggml-small", "large-v3-q5_0"])
    def test_normal_names_are_accepted(self, name):
        from src_backends.config import is_valid_vibe_model
        assert is_valid_vibe_model(name) is True

    def test_api_rejects_unsafe_model_names_and_saves_nothing(self, test_client):
        from src_backends.config import load_config
        r = test_client.post("/config", json={"vibe_model": "../../x", "dark_mode": True})
        assert r.status_code == 400
        assert load_config().vibe_model == "large-v3-turbo" and load_config().dark_mode is False

    def test_unsafe_names_never_reach_the_file_system_helpers(self, folder):
        assert vibe_client.find_model_file("../../x") is None
        assert vibe_client.model_status("../../x") == "missing"
        with pytest.raises(RuntimeError):
            vibe_client.ensure_model_downloaded("../../x")

    def test_prefixed_name_is_normalized_for_status_and_download(self, folder, monkeypatch):
        _make_model(folder, "tiny")
        assert vibe_client.model_status("ggml-tiny") == "ready"
        monkeypatch.setattr(vibe_client, "find_vibe_binary_dir", lambda: None)
        assert vibe_client.ensure_model_downloaded("ggml-tiny").name == "ggml-tiny.bin"


class TestSecondReview:
    def test_download_progress_uses_its_own_fields_not_the_job_progress(self, main_mod, monkeypatch):
        seen = []

        def fake_download(name, progress_callback=None):
            progress_callback("x", 40)
            seen.append((main_mod.processing_status["vibe_model_downloading"],
                         main_mod.processing_status["vibe_model_progress"]))

        fake = _fake_vibe()
        fake.ensure_model_downloaded = fake_download
        monkeypatch.setattr(main_mod, "vibe_client", fake)
        monkeypatch.setattr(main_mod, "load_config", lambda: AppConfig(vibe_model="small"))
        monkeypatch.setattr(main_mod, "_apply_vibe_model_change", lambda: None)
        job = main_mod.processing_status
        monkeypatch.setitem(job, "message", "処理中")
        monkeypatch.setitem(job, "current_step", "transcribe")
        monkeypatch.setitem(job, "step_progress", 10)
        monkeypatch.setitem(job, "is_downloading", False)
        _run(main_mod._download_vibe_model("medium"))
        assert seen == [("medium", 40)]
        # 処理の進捗は触らない
        assert job["message"] == "処理中" and job["current_step"] == "transcribe" and job["step_progress"] == 10
        assert job["is_downloading"] is False
        assert job["vibe_model_downloading"] == "" and job["vibe_model_progress"] == 0

    def test_notice_is_kept_when_another_model_finishes_downloading(self, main_mod, monkeypatch):
        monkeypatch.setattr(main_mod, "vibe_client", _fake_vibe())
        monkeypatch.setattr(main_mod, "load_config", lambda: AppConfig(vibe_model="small"))
        monkeypatch.setattr(main_mod, "_apply_vibe_model_change", lambda: None)
        monkeypatch.setitem(main_mod.processing_status, "vibe_model_notice", "small を使えません")
        _run(main_mod._download_vibe_model("medium"))
        assert main_mod.processing_status["vibe_model_notice"] == "small を使えません"

    def test_python_fallback_returns_to_vibe_when_a_model_appears_later(self, main_mod, monkeypatch):
        calls = []

        async def fake_start(config, loop, binary_checked=False):
            calls.append(config.vibe_model)

        monkeypatch.setattr(main_mod, "_start_vibe_server", fake_start)
        monkeypatch.setattr(main_mod, "_vibe_model_usable", lambda config: True)
        monkeypatch.setattr(main_mod, "_vibe_startup_task", None)
        monkeypatch.setattr(main_mod, "_vibe_ready", True)
        monkeypatch.setattr(main_mod, "load_config", lambda: AppConfig(vibe_model="small"))
        monkeypatch.setitem(main_mod.processing_status, "vibe_fallback_active", True)
        running = _fake_vibe()
        running.is_vibe_running = lambda: True
        monkeypatch.setattr(main_mod, "vibe_client", running)
        _run(main_mod.ensure_vibe_ready())
        assert calls == ["small"]
        assert main_mod.processing_status["vibe_fallback_active"] is False

    def test_python_fallback_stays_when_no_model_is_usable(self, main_mod, monkeypatch):
        calls = []

        async def fake_start(config, loop, binary_checked=False):
            calls.append(1)

        monkeypatch.setattr(main_mod, "_start_vibe_server", fake_start)
        monkeypatch.setattr(main_mod, "_vibe_model_usable", lambda config: False)
        monkeypatch.setattr(main_mod, "_vibe_ready", True)
        monkeypatch.setattr(main_mod, "load_config", lambda: AppConfig())
        monkeypatch.setitem(main_mod.processing_status, "vibe_fallback_active", True)
        _run(main_mod.ensure_vibe_ready())
        assert calls == [] and main_mod.processing_status["vibe_fallback_active"] is True

    def test_start_vibe_does_not_stop_the_running_server_when_the_new_model_is_missing(self, monkeypatch, tmp_path):
        stopped = []
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_model_name", "large-v3-turbo")
        monkeypatch.setattr(vibe_client, "_vibe_port", 1234)
        monkeypatch.setattr(vibe_client, "stop_vibe", lambda: stopped.append(1))
        monkeypatch.setattr(vibe_client, "find_vibe_binary_dir", lambda: None)
        monkeypatch.setattr(vibe_client, "model_search_dirs", lambda d=None: [tmp_path])
        with pytest.raises(RuntimeError):
            vibe_client.start_vibe(model_name="large-v3")
        assert stopped == []

    def test_saved_form_guard_escape_and_flags_in_the_frontend(self):
        from pathlib import Path
        app = (Path(__file__).resolve().parent.parent / "src" / "app.js").read_text(encoding="utf-8-sig")
        assert "if (savingModelSettings) return false;" in app  # Ctrl+S の連打で重ねて保存しない
        assert "vibe-model-download-modal" in app.split("e.key === 'Escape'")[1]  # Esc でダイアログを閉じる
        assert app.count("vibeDownloadStarted = false;") >= 3  # 保存に失敗したら印を戻す
        js = (Path(__file__).resolve().parent.parent / "src" / "i18n.js").read_text(encoding="utf-8-sig")
        assert js.count('"app.vibe_dl_progress"') == 2



class TestWizardModelChoice:
    def _read(self, rel):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8-sig")

    def test_wizard_has_no_duplicate_turbo_cards(self):
        """「Large V3 Turbo（推奨）」と「large-v3-turbo」は同じモデル。推奨の1枚だけ残す"""
        html = self._read("src/wizard.html")
        values = re.findall(r'whisper-model-option" data-value="([^"]+)"', html)
        assert values == ["tiny", "base", "small", "large-v3-turbo"]
        assert re.search(r'selection-card selected whisper-model-option" data-value="large-v3-turbo"', html)
        assert 'data-i18n="wiz.selection_title"' in html and "large-v3-turbo</div>" not in html

    def test_wizard_saves_the_vibe_model_and_confirms_the_download(self):
        js = self._read("src/wizard.js")
        assert "whisperModel: 'large-v3-turbo'" in js
        assert "vibe_model: state.whisperModel" in js
        assert "whisper_model: state.whisperModel === 'large-v3-turbo' ? 'turbo' : state.whisperModel" in js
        assert "confirmModelDownload(state.whisperModel)" in js
        assert "/vibe/model/status" in js and "/vibe/model/download" in js
        html = self._read("src/wizard.html")
        assert 'id="model-download-modal"' in html
        assert 'id="btn-model-dl-download"' in html and 'id="btn-model-dl-cancel"' in html

    def test_wizard_save_stores_the_vibe_model_and_applies_it(self, test_client, monkeypatch):
        import src_backends.main as m
        from src_backends.config import load_config
        applied = []
        monkeypatch.setattr(m, "_apply_vibe_model_change", lambda: applied.append(1))
        r = test_client.post("/wizard/save", json={"vibe_model": "small", "whisper_model": "small"})
        assert r.json()["status"] == "ok"
        assert load_config().vibe_model == "small" and load_config().whisper_model == "small"
        assert applied == [1]

    def test_wizard_save_without_a_model_change_does_not_restart(self, test_client, monkeypatch):
        import src_backends.main as m
        applied = []
        monkeypatch.setattr(m, "_apply_vibe_model_change", lambda: applied.append(1))
        test_client.post("/wizard/save", json={"vibe_model": "large-v3-turbo", "whisper_model": "turbo"})
        assert applied == []

    def test_wizard_save_rejects_unsafe_model_names(self, test_client):
        from src_backends.config import load_config
        r = test_client.post("/wizard/save", json={"vibe_model": "../../x"})
        assert r.status_code == 400
        assert load_config().vibe_model == "large-v3-turbo" and load_config().wizard_completed is False
