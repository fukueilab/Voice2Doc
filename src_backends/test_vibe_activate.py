"""Vibe 導入後の再確認（POST /vibe/activate）と、検出の強化・手動指定・画面側の呼び出しのテスト

実際の Vibe は動かさない。main.vibe_client は conftest.test_client が MagicMock に差し替えるので、
そこへ検出結果・モデルの状態・起動の成否を設定して振る舞いを確かめる。"""
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src_backends import main as main_mod
from src_backends import vibe_client

SRC = Path(__file__).resolve().parent.parent / "src"
WIZARD_JS = (SRC / "wizard.js").read_text(encoding="utf-8-sig")
WIZARD_HTML = (SRC / "wizard.html").read_text(encoding="utf-8-sig")
APP_JS = (SRC / "app.js").read_text(encoding="utf-8-sig")
INDEX_HTML = (SRC / "index.html").read_text(encoding="utf-8-sig")


@pytest.fixture
def vibe(test_client, write_config, monkeypatch, tmp_path):
    """vibe_client（モック）に「見つかった」状態を設定する。返す値で各テストが上書きできる"""
    write_config()
    exe = tmp_path / "vibe" / "vibe-server.exe"
    exe.parent.mkdir()
    exe.write_text("")
    mock = main_mod.vibe_client
    mock.detect_vibe_binary.return_value = (exe, [str(exe)])
    mock.model_status.return_value = "ready"
    mock.is_vibe_running.return_value = False
    mock.start_vibe.return_value = 12345
    monkeypatch.setattr(main_mod, "_vibe_startup_task", None)
    monkeypatch.setattr(main_mod, "_vibe_ready", False)
    main_mod.processing_status["vibe_fallback_active"] = True
    yield mock, exe, test_client
    main_mod.processing_status["vibe_fallback_active"] = False


class TestActivateEndpoint:
    def test_not_found_returns_searched_locations(self, vibe):
        mock, _, client = vibe
        mock.detect_vibe_binary.return_value = (None, ["C:/a/vibe-server.exe", "registry(HKLM): C:/b"])
        body = client.post("/vibe/activate").json()
        assert body["status"] == "not_found"
        assert body["searched"] == ["C:/a/vibe-server.exe", "registry(HKLM): C:/b"]
        mock.start_vibe.assert_not_called()
        assert main_mod.processing_status["vibe_fallback_active"] is True  # 見つからなければ印は戻さない

    def test_model_downloading_does_not_start_and_keeps_fallback(self, vibe):
        mock, _, client = vibe
        mock.model_status.return_value = "downloading"
        body = client.post("/vibe/activate").json()
        assert body["status"] == "model_downloading"
        mock.start_vibe.assert_not_called()
        assert main_mod.processing_status["vibe_fallback_active"] is True
        assert main_mod._vibe_ready is False

    def test_model_missing_and_nothing_else_downloaded_is_reported_as_downloading(self, vibe):
        mock, _, client = vibe
        mock.model_status.return_value = "missing"
        mock.ready_models.return_value = []  # 取得済みのモデルが1つも無い（Vibe のダウンロード待ち）
        assert client.post("/vibe/activate").json()["status"] == "model_downloading"
        mock.start_vibe.assert_not_called()

    def test_model_missing_but_another_model_is_downloaded_starts_with_it(self, vibe):
        """設定のモデルが無くても、取得済みの別のモデルがあれば、待たずにそれで起動する"""
        mock, _, client = vibe
        mock.model_status.return_value = "missing"
        mock.ready_models.return_value = ["medium"]
        mock.find_vibe_binary_dir.return_value = "dir"
        mock.find_model_file.return_value = None  # 設定のモデルは無い
        mock.start_vibe.side_effect = [RuntimeError("no file"), 1]  # 設定のモデルは起動できず、別のモデルで起動
        assert client.post("/vibe/activate").json()["status"] == "ok"
        assert [c.kwargs["model_name"] for c in mock.start_vibe.call_args_list] == ["large-v3-turbo", "medium"]
        assert "medium" in main_mod.processing_status["vibe_model_notice"]
        mock.start_vibe.side_effect = None
        main_mod.processing_status["vibe_model_notice"] = ""

    def test_ok_starts_server_clears_fallback_and_saves_path(self, vibe):
        mock, exe, client = vibe
        body = client.post("/vibe/activate").json()
        assert body["status"] == "ok"
        assert body["path"] == str(exe)
        mock.start_vibe.assert_called_once()
        assert main_mod.processing_status["vibe_fallback_active"] is False
        assert main_mod._vibe_ready is True
        assert client.get("/config").json()["vibe_server_path"] == str(exe)

    def test_already_running_is_not_started_twice(self, vibe):
        mock, _, client = vibe
        mock.is_vibe_running.return_value = True
        assert client.post("/vibe/activate").json()["status"] == "ok"
        mock.start_vibe.assert_not_called()
        assert main_mod.processing_status["vibe_fallback_active"] is False

    def test_second_call_after_success_does_not_double_start(self, vibe):
        mock, _, client = vibe
        client.post("/vibe/activate")
        mock.is_vibe_running.return_value = True  # 1回目で起動した状態
        assert client.post("/vibe/activate").json()["status"] == "ok"
        assert mock.start_vibe.call_count == 1

    def test_start_failure_returns_error_and_keeps_fallback(self, vibe):
        mock, _, client = vibe
        mock.start_vibe.side_effect = RuntimeError("boom")
        body = client.post("/vibe/activate").json()
        assert body["status"] == "error"
        assert "boom" in body["message"]
        assert main_mod.processing_status["vibe_fallback_active"] is True
        assert main_mod._vibe_ready is False

    def test_returns_starting_while_startup_task_is_running(self, vibe, monkeypatch):
        mock, _, client = vibe
        pending = MagicMock()
        pending.done.return_value = False
        monkeypatch.setattr(main_mod, "_vibe_startup_task", pending)
        assert client.post("/vibe/activate").json()["status"] == "starting"
        mock.start_vibe.assert_not_called()

    def test_manual_path_is_saved_and_used(self, vibe, tmp_path):
        mock, _, client = vibe
        manual = tmp_path / "elsewhere" / "vibe-server.exe"
        manual.parent.mkdir()
        manual.write_text("")
        mock.detect_vibe_binary.return_value = (manual, [f"settings: {manual}"])
        body = client.post("/vibe/activate", json={"path": str(manual)}).json()
        assert body["status"] == "ok"
        assert client.get("/config").json()["vibe_server_path"] == str(manual)
        assert mock.detect_vibe_binary.call_args.args[0] == str(manual)

    def test_manual_path_with_another_file_name_is_rejected(self, vibe, tmp_path):
        mock, _, client = vibe
        wrong = tmp_path / "vibe.exe"
        wrong.write_text("")
        body = client.post("/vibe/activate", json={"path": str(wrong)}).json()
        assert body["status"] == "not_found"
        assert client.get("/config").json()["vibe_server_path"] == ""  # 保存しない
        mock.start_vibe.assert_not_called()

    def test_missing_model_is_not_downloaded_automatically(self, vibe, monkeypatch):
        """モデルは自動ではダウンロードしない（Vibe 自身のダウンロードを待つ。アプリでの取得は設定で利用者が選ぶ）"""
        mock, _, client = vibe
        mock.model_status.return_value = "missing"
        mock.ready_models.return_value = []
        started = []
        monkeypatch.setattr(main_mod, "_start_vibe_model_download", lambda model: started.append(model))
        body = client.post("/vibe/activate").json()
        assert body["status"] == "model_downloading"  # 起動はせず、待つ
        assert started == []
        mock.start_vibe.assert_not_called()

    def test_manual_path_that_does_not_exist_is_not_found(self, vibe, tmp_path):
        mock, _, client = vibe
        body = client.post("/vibe/activate", json={"path": str(tmp_path / "nothing.exe")}).json()
        assert body["status"] == "not_found"
        mock.start_vibe.assert_not_called()


class TestWizardSaveActivatesVibe:
    def _wait(self, predicate, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_wizard_save_calls_activate_when_vibe_is_found(self, vibe, monkeypatch):
        mock, _, client = vibe
        activate = AsyncMock()
        monkeypatch.setattr(main_mod, "activate_vibe", activate)
        resp = client.post("/wizard/save", json={"whisper_backend": "vibe"})
        assert resp.json()["status"] == "ok"
        assert self._wait(lambda: activate.await_count == 1)

    def test_wizard_save_skips_activate_when_vibe_is_missing(self, vibe, monkeypatch):
        mock, _, client = vibe
        mock.find_vibe_binary.return_value = None
        activate = AsyncMock()
        monkeypatch.setattr(main_mod, "activate_vibe", activate)
        assert client.post("/wizard/save", json={}).json()["status"] == "ok"
        time.sleep(0.2)
        activate.assert_not_awaited()


# ---------------------------------------------------------------- vibe_client の検出

@pytest.fixture
def clean_detection(monkeypatch):
    """固定の候補・追加の場所を空にして、テストごとに必要な場所だけ足す"""
    monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [])
    monkeypatch.setattr(vibe_client, "_find_vibe_install_dir_from_registry", lambda: None)
    monkeypatch.setattr(vibe_client, "_find_vibe_install_dirs_from_hklm", lambda: [])
    monkeypatch.setattr(vibe_client, "_program_files_candidates", lambda: [])
    monkeypatch.setattr(vibe_client, "_start_menu_shortcut_targets", lambda: [])


def _exe(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "vibe-server.exe"
    path.write_text("")
    return path


class TestExtendedDetection:
    def test_found_in_hklm_install_dir(self, clean_detection, monkeypatch, tmp_path):
        exe = _exe(tmp_path / "hklm")
        monkeypatch.setattr(vibe_client, "_find_vibe_install_dirs_from_hklm", lambda: [exe.parent])
        found, searched = vibe_client.detect_vibe_binary()
        assert found == exe
        assert any(s.startswith("registry(HKLM)") for s in searched)

    def test_found_in_program_files(self, clean_detection, monkeypatch, tmp_path):
        exe = _exe(tmp_path / "Program Files" / "vibe")
        monkeypatch.setattr(vibe_client, "_program_files_candidates", lambda: [exe])
        assert vibe_client.find_vibe_binary() == exe

    def test_found_via_start_menu_shortcut(self, clean_detection, monkeypatch, tmp_path):
        exe = _exe(tmp_path / "shortcut_target")
        monkeypatch.setattr(vibe_client, "_start_menu_shortcut_targets", lambda: [exe])
        assert vibe_client.find_vibe_binary() is None  # 通常の検出は時間のかかるスタートメニューを調べない
        found, searched = vibe_client.detect_vibe_binary(deep=True)
        assert found == exe
        assert any(s.startswith("Start menu shortcut") for s in searched)

    def test_not_found_returns_every_searched_location(self, clean_detection, monkeypatch, tmp_path):
        monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [tmp_path / "a.exe"])
        monkeypatch.setattr(vibe_client, "_program_files_candidates", lambda: [tmp_path / "pf" / "vibe-server.exe"])
        found, searched = vibe_client.detect_vibe_binary(str(tmp_path / "custom.exe"))
        assert found is None
        assert searched[0] == f"settings: {tmp_path / 'custom.exe'}"
        assert str(tmp_path / "a.exe") in searched
        assert any("Program Files" in s for s in searched)

    def test_saved_path_is_preferred_over_autodetect(self, clean_detection, monkeypatch, tmp_path):
        saved = _exe(tmp_path / "saved")
        auto = _exe(tmp_path / "auto")
        monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [auto])
        assert vibe_client.find_vibe_binary(str(saved)) == saved

    def test_vanished_saved_path_falls_back_to_autodetect(self, clean_detection, monkeypatch, tmp_path):
        auto = _exe(tmp_path / "auto")
        monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [auto])
        assert vibe_client.find_vibe_binary(str(tmp_path / "gone" / "vibe-server.exe")) == auto


class TestModelFileComplete:
    def test_partial_and_complete(self, tmp_path):
        small = tmp_path / "ggml-tiny.bin"
        small.write_bytes(b"x" * 10)
        assert vibe_client._model_file_complete(small, "tiny") is False
        with open(small, "wb") as f:
            f.truncate(vibe_client._MODEL_EXPECTED_BYTES["tiny"])
        assert vibe_client._model_file_complete(small, "tiny") is True
        (tmp_path / "ggml-tiny.bin.part").write_bytes(b"x")
        assert vibe_client._model_file_complete(small, "tiny") is False


class TestModelStatus:
    @pytest.fixture(autouse=True)
    def _dirs(self, monkeypatch, tmp_path):
        self.dir = tmp_path / "models"
        self.dir.mkdir()
        monkeypatch.setattr(vibe_client, "model_search_dirs", lambda _bin_dir=None: [self.dir])

    def test_missing(self):
        assert vibe_client.model_status("large-v3-turbo") == "missing"

    def test_complete_file_is_ready(self):
        size = vibe_client._MODEL_EXPECTED_BYTES["tiny"]
        with open(self.dir / "ggml-tiny.bin", "wb") as f:
            f.truncate(size)
        assert vibe_client.model_status("tiny") == "ready"

    def test_undersized_file_is_downloading(self):
        (self.dir / "ggml-tiny.bin").write_bytes(b"x" * 1024)
        assert vibe_client.model_status("tiny") == "downloading"

    def test_empty_file_is_downloading(self):
        (self.dir / "ggml-tiny.bin").write_bytes(b"")
        assert vibe_client.model_status("tiny") == "downloading"

    def test_temp_sibling_means_downloading_even_if_size_is_enough(self):
        size = vibe_client._MODEL_EXPECTED_BYTES["tiny"]
        with open(self.dir / "ggml-tiny.bin", "wb") as f:
            f.truncate(size)
        (self.dir / "ggml-tiny.bin.part").write_bytes(b"x")
        assert vibe_client.model_status("tiny") == "downloading"

    def test_only_a_part_file_is_downloading(self):
        (self.dir / "ggml-tiny.bin.part").write_bytes(b"x")
        assert vibe_client.model_status("tiny") == "downloading"

    def test_unknown_model_with_a_file_is_ready(self):
        (self.dir / "ggml-custom.bin").write_bytes(b"x" * 10)
        assert vibe_client.model_status("custom") == "ready"


# ---------------------------------------------------------------- 画面側（静的検査）

class TestWizardFrontend:
    def test_next_is_disabled_until_all_four_prerequisites_are_ready(self):
        assert "prereqAllReady = !!(data.vc_runtime && data.webview2 && data.ffmpeg && data.vibe)" in WIZARD_JS
        assert "btnNext.disabled = nextBlocked()" in WIZARD_JS
        gate = r"function nextBlocked\(\)\s*\{\s*return currentPage === 2 && !prereqAllReady && !skipPrereqCheck;"
        assert re.search(gate, WIZARD_JS)
        # 無効のままでは進めない
        assert re.search(r"function goNext\(\)\s*\{\s*if \(nextBlocked\(\)\) return;", WIZARD_JS)

    def test_prereq_check_failure_keeps_next_disabled(self):
        catch = WIZARD_JS[WIZARD_JS.index(".catch(function () {\n        prereqAllReady = false;"):]
        assert "prereqAllReady = false" in catch[:200]

    def test_skip_link_exists_and_enables_next(self):
        assert 'id="btn-skip-check"' in WIZARD_HTML
        assert "skipPrereqCheck = true" in WIZARD_JS

    def test_recheck_calls_activate_after_prereq_check_when_vibe_found(self):
        assert "if (data.vibe || explicit === true) activateVibe();" in WIZARD_JS
        assert "'/vibe/activate'" in WIZARD_JS

    def test_manual_path_button_uses_exe_dialog_then_activates(self):
        assert 'id="btn-vibe-pick"' in WIZARD_HTML
        assert "kind: 'exe'" in WIZARD_JS
        assert "activateVibe(path)" in WIZARD_JS and "body = path ? { path: path } : {}" in WIZARD_JS

    def test_model_downloading_is_retried_automatically(self):
        assert "model_downloading" in WIZARD_JS and "ACTIVATE_RETRY_MS" in WIZARD_JS

    def test_welcome_page_has_vibe_first_box_and_next_hint(self):
        assert 'data-i18n="wiz.first_need_title"' in WIZARD_HTML
        assert 'data-i18n="wiz.first_next_hint"' in WIZARD_HTML


class TestMainScreenFrontend:
    def test_fallback_modal_has_recheck_button_that_calls_activate(self):
        modal = INDEX_HTML[INDEX_HTML.index('id="vibe-fallback-modal"'):]
        assert 'id="btn-vibe-recheck"' in modal[:1500]
        assert "'/vibe/activate'" in APP_JS
        assert re.search(r"result\.status === 'ok'\)\s*\{\s*document\.getElementById\('vibe-fallback-modal'\)", APP_JS)


class TestStrings:
    def _dictionary(self):
        from src_backends.test_i18n import FRONT
        return FRONT

    def test_new_strings_exist_in_both_languages_and_english_has_no_japanese(self):
        front = self._dictionary()
        japanese = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
        keys = [k for k in front["ja"] if k.startswith(("wiz.vibe_", "wiz.first_", "wiz.skip_check", "wiz.next_blocked",
                                                       "app.vibe_", "idx.btn_vibe_recheck"))]
        assert len(keys) >= 15
        for k in keys:
            assert k in front["en"], k
            assert not japanese.search(front["en"][k]), k

    def test_recommended_model_label(self):
        front = self._dictionary()
        assert front["ja"]["wiz.selection_title"] == "Large V3 Turbo（推奨）"
        assert front["en"]["wiz.selection_title"] == "Large V3 Turbo (recommended)"

    def test_guide_texts_say_what_to_do(self):
        front = self._dictionary()
        assert "Assets" in front["ja"]["wiz.vibe_missing"] and "Assets" in front["en"]["wiz.vibe_missing"]
        assert front["ja"]["wiz.vibe_open"] == "Vibe の導入が必要です。Vibe のインストーラーがあるページを開く"
        assert front["ja"]["wiz.vibe_pick"] == "vibe-server.exe の場所を指定する"
        assert front["ja"]["wiz.skip_check"] == "確認せずに進む"

    def test_backend_messages_exist_in_both_languages(self):
        from src_backends import i18n
        keys = ["vibe.activate_ok", "vibe.activate_not_found", "vibe.activate_bad_path",
                "vibe.activate_downloading", "vibe.activate_starting", "vibe.activate_failed"]
        for key in keys:
            ja = i18n.tr("ja", key, path="p", error="e")
            en = i18n.tr("en", key, path="p", error="e")
            assert ja != key and en != key and ja != en
