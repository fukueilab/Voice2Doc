"""起動時に画面が白いままになる問題の対策のテスト

GUI（PyWebView）を使わずに、起動の判定ロジック（src_backends/startup.py）とサーバー側の合図を検査する。
"""
import http.server
import json
import re
import socket
import threading
from pathlib import Path

import pytest

from src_backends import app_logging, startup

ROOT = Path(__file__).resolve().parent.parent


# ==================== /health の識別 ====================

class TestHealthIdentity:
    def test_health_has_app_and_instance_id(self, test_client):
        data = test_client.get("/health").json()
        assert data["app"] == "voice2doc"
        assert data["instance_id"] == startup.INSTANCE_ID
        assert data["safe_render"] is False

    def test_instance_id_is_random_per_process_value(self):
        assert re.fullmatch(r"[0-9a-f]{32}", startup.INSTANCE_ID)


# ==================== wait_for_server ====================

class TestWaitForServer:
    def _wait(self, fetch, alive=lambda: True, timeout=5.0):
        now = [0.0]

        def sleep(sec):
            now[0] += sec

        return startup.wait_for_server(
            8008, "me", alive, timeout=timeout, interval=1.0,
            fetch_health=fetch, sleep=sleep, clock=lambda: now[0])

    def test_ok_when_own_instance_answers(self):
        assert self._wait(lambda p: {"app": "voice2doc", "instance_id": "me"}) == startup.WAIT_OK

    def test_other_voice2doc_instance_is_not_success(self):
        """別プロセス(前回の残骸)が同じポートで /health に答えても成功にしない"""
        result = self._wait(lambda p: {"app": "voice2doc", "instance_id": "someone-else"})
        assert result == startup.WAIT_TIMEOUT

    def test_other_program_answering_is_not_success(self):
        assert self._wait(lambda p: {"status": "ok"}) == startup.WAIT_TIMEOUT

    def test_dead_thread_fails_immediately(self):
        calls = []
        result = self._wait(lambda p: calls.append(p) or None, alive=lambda: False)
        assert result == startup.WAIT_THREAD_DIED
        assert calls == []  # 待たずに分かる

    def test_thread_dying_while_waiting(self):
        alive = iter([True, True, False])
        result = self._wait(lambda p: None, alive=lambda: next(alive))
        assert result == startup.WAIT_THREAD_DIED

    def test_becomes_ready_after_retries(self):
        answers = iter([None, None, {"app": "voice2doc", "instance_id": "me"}])
        assert self._wait(lambda p: next(answers)) == startup.WAIT_OK


# ==================== ポート ====================

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b"{}"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


def _serve(body: dict):
    handler = type("H", (_Handler,), {"body": json.dumps(body).encode()})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class TestPorts:
    def test_probe_free_port(self):
        assert startup.probe_port(_free_port()) == startup.PORT_FREE

    def test_probe_other_program(self):
        server = _serve({"hello": "world"})
        try:
            assert startup.probe_port(server.server_address[1]) == startup.PORT_OTHER
        finally:
            server.shutdown()
            server.server_close()

    def test_probe_voice2doc(self):
        server = _serve({"app": "voice2doc"})
        try:
            assert startup.probe_port(server.server_address[1]) == startup.PORT_VOICE2DOC
        finally:
            server.shutdown()
            server.server_close()

    def test_select_first_free(self):
        sel = startup.select_port(probe=lambda p: startup.PORT_FREE)
        assert (sel.port, sel.first_holder) == (8008, startup.PORT_FREE)

    def test_select_skips_busy_ports(self):
        busy = {8008: startup.PORT_OTHER, 8009: startup.PORT_VOICE2DOC}
        sel = startup.select_port(probe=lambda p: busy.get(p, startup.PORT_FREE))
        assert sel.port == 8010
        assert sel.first_holder == startup.PORT_OTHER

    def test_select_voice2doc_holding_first_port(self):
        sel = startup.select_port(probe=lambda p: startup.PORT_VOICE2DOC if p == 8008 else startup.PORT_FREE)
        assert (sel.port, sel.first_holder) == (8009, startup.PORT_VOICE2DOC)

    def test_select_all_busy(self):
        sel = startup.select_port(probe=lambda p: startup.PORT_OTHER)
        assert sel.port is None

    def test_select_range_is_8008_to_8030(self):
        seen = []
        startup.select_port(probe=lambda p: seen.append(p) or startup.PORT_OTHER)
        assert (seen[0], seen[-1]) == (8008, 8030)


class TestCors:
    @pytest.fixture(autouse=True)
    def restore_cors(self):
        from src_backends.main import configure_cors

        yield
        configure_cors(startup.DEFAULT_PORT)

    def _allowed(self, client, origin):
        resp = client.get("/health", headers={"Origin": origin})
        return resp.headers.get("access-control-allow-origin")

    def test_default_port_origins(self, test_client):
        assert self._allowed(test_client, "http://127.0.0.1:8008") == "http://127.0.0.1:8008"
        assert self._allowed(test_client, "http://127.0.0.1:8012") is None

    def test_allow_list_follows_chosen_port(self):
        from starlette.testclient import TestClient

        from src_backends.main import app, configure_cors

        configure_cors(8012)
        with TestClient(app, base_url="http://127.0.0.1:8012") as client:
            assert self._allowed(client, "http://127.0.0.1:8012") == "http://127.0.0.1:8012"
            assert self._allowed(client, "http://localhost:8012") == "http://localhost:8012"
            assert self._allowed(client, "http://127.0.0.1:8008") is None

    def test_configure_cors_does_not_stack_middleware(self):
        from starlette.middleware.cors import CORSMiddleware

        from src_backends.main import app, configure_cors

        configure_cors(8010)
        configure_cors(8011)
        assert sum(1 for m in app.user_middleware if m.cls is CORSMiddleware) == 1


# ==================== WebView2 のデータフォルダ ====================

class TestUserDataCleanup:
    def _make(self, root, *names):
        for n in names:
            (root / n).mkdir(parents=True)
            (root / n / "x.dat").write_text("x")

    def test_removes_only_dead_process_folders(self, tmp_path):
        self._make(tmp_path, "run-100", "run-200", "run-300")
        removed = startup.cleanup_stale_userdata(tmp_path, own_pid=300, is_alive=lambda pid: pid == 200)
        assert removed == ["run-100"]
        assert not (tmp_path / "run-100").exists()
        assert (tmp_path / "run-200").exists()  # 生きている
        assert (tmp_path / "run-300").exists()  # 自分

    def test_ignores_unrelated_names_and_files(self, tmp_path):
        self._make(tmp_path, "EBWebView", "run-abc", "run-")
        (tmp_path / "run-5").write_text("file, not a dir")
        assert startup.cleanup_stale_userdata(tmp_path, own_pid=1, is_alive=lambda pid: False) == []
        assert (tmp_path / "EBWebView").exists() and (tmp_path / "run-abc").exists()

    def test_deletion_failure_is_ignored(self, tmp_path, monkeypatch):
        self._make(tmp_path, "run-100", "run-101")

        def boom(path, *a, **k):
            if path.name == "run-100":
                raise PermissionError("in use")
            for child in path.iterdir():
                child.unlink()
            path.rmdir()

        monkeypatch.setattr(startup.shutil, "rmtree", boom)
        removed = startup.cleanup_stale_userdata(tmp_path, own_pid=1, is_alive=lambda pid: False)
        assert removed == ["run-101"]  # 失敗しても残りを続ける
        assert (tmp_path / "run-100").exists()

    def test_missing_root_is_fine(self, tmp_path):
        assert startup.cleanup_stale_userdata(tmp_path / "none") == []

    def test_userdata_dir_is_per_pid(self):
        assert startup.userdata_dir(4321).name == "run-4321"
        assert startup.userdata_dir(4321).parent.name == ".voice2doc"

    def test_pid_alive_for_self_and_impossible_pid(self):
        import os

        assert startup.pid_alive(os.getpid()) is True
        assert startup.pid_alive(0) is False


# ==================== 見張り役 ====================

class TestWatchdog:
    def _run(self, ready_after=None, already_safe=False, relaunch_ok=True, diagnose_raises=False):
        """ready_after: 何回目の wait_ready で合図が来るか(1始まり)。None は来ない"""
        log = []
        count = [0]

        def wait_ready(timeout):
            count[0] += 1
            log.append(("wait", timeout))
            return ready_after is not None and count[0] >= ready_after

        def diagnose():
            log.append("diagnose")
            if diagnose_raises:
                raise RuntimeError("evaluate_js hung")

        outcome = startup.run_watchdog(
            wait_ready=wait_ready,
            diagnose=diagnose,
            reload_page=lambda: log.append("reload"),
            relaunch_safe=lambda: log.append("relaunch") or relaunch_ok,
            show_failure=lambda: log.append("failure"),
            already_safe=already_safe,
        )
        return outcome, log

    def test_ready_in_time_does_nothing(self):
        outcome, log = self._run(ready_after=1)
        assert outcome == startup.WATCHDOG_READY
        assert log == [("wait", 10.0)]

    def test_reload_fixes_it(self):
        outcome, log = self._run(ready_after=2)
        assert outcome == startup.WATCHDOG_READY_AFTER_RELOAD
        assert log == [("wait", 10.0), "diagnose", "reload", ("wait", 8.0)]

    def test_safe_relaunch_when_reload_fails(self):
        outcome, log = self._run(ready_after=None)
        assert outcome == startup.WATCHDOG_RELAUNCHED
        assert log[-1] == "relaunch" and "failure" not in log

    def test_safe_relaunch_only_once(self):
        """安全モードで起動した後は再起動しない(ループ防止)。案内を出す"""
        outcome, log = self._run(ready_after=None, already_safe=True)
        assert outcome == startup.WATCHDOG_FAILED
        assert "relaunch" not in log and log[-1] == "failure"

    def test_relaunch_failure_shows_guidance(self):
        outcome, log = self._run(ready_after=None, relaunch_ok=False)
        assert outcome == startup.WATCHDOG_FAILED
        assert log[-2:] == ["relaunch", "failure"]

    def test_diagnose_failure_does_not_stop_recovery(self):
        outcome, log = self._run(ready_after=2, diagnose_raises=True)
        assert outcome == startup.WATCHDOG_READY_AFTER_RELOAD
        assert "reload" in log

    def test_evaluate_with_timeout(self):
        assert startup.evaluate_with_timeout(lambda: "ok", 1.0) == "ok"
        assert startup.evaluate_with_timeout(lambda: 1 / 0, 1.0) is None
        release = threading.Event()
        assert startup.evaluate_with_timeout(lambda: release.wait(5), 0.05) is None  # 固まったら諦める
        release.set()


# ==================== /ui-ready ====================

class TestUiReady:
    @pytest.fixture(autouse=True)
    def reset(self):
        startup.reset_ui_ready()
        yield
        startup.reset_ui_ready()

    def test_ui_ready_accepted(self, test_client):
        assert not startup.is_ui_ready()
        resp = test_client.post("/ui-ready", json={"instance_id": startup.INSTANCE_ID})
        assert resp.status_code == 200
        assert startup.is_ui_ready()

    def test_wrong_instance_id_rejected(self, test_client):
        resp = test_client.post("/ui-ready", json={"instance_id": "stale-page"})
        assert resp.status_code == 400
        assert not startup.is_ui_ready()

    def test_missing_instance_id_rejected(self, test_client):
        assert test_client.post("/ui-ready", json={}).status_code == 422
        assert not startup.is_ui_ready()

    def test_test_switch_ignores_signal(self, test_client, monkeypatch):
        monkeypatch.setenv(startup.TEST_IGNORE_UI_READY_ENV, "1")
        resp = test_client.post("/ui-ready", json={"instance_id": startup.INSTANCE_ID})
        assert resp.status_code == 200
        assert not startup.is_ui_ready()

    def test_wait_ui_ready(self):
        assert startup.wait_ui_ready(0.01) is False
        startup.mark_ui_ready()
        assert startup.wait_ui_ready(0.01) is True


# ==================== 描画の安全モード ====================

class TestSafeRender:
    def test_marker_lifecycle_and_health(self, test_client):
        assert startup.safe_render_marker_exists() is False
        assert startup.write_safe_render_marker() is True
        assert startup.safe_render_marker_exists() is True
        assert test_client.get("/health").json()["safe_render"] is True

    def test_reset_endpoint_removes_marker(self, test_client):
        startup.write_safe_render_marker()
        resp = test_client.post("/safe-render/reset")
        assert resp.status_code == 200 and resp.json()["safe_render"] is False
        assert startup.safe_render_marker_exists() is False
        assert test_client.post("/safe-render/reset").status_code == 200  # 無くても成功

    def test_requested_by_env_or_marker(self, isolate_config):
        assert startup.safe_render_requested({}) is False
        assert startup.safe_render_requested({startup.SAFE_RENDER_ENV: "1"}) is True
        startup.write_safe_render_marker()
        assert startup.safe_render_requested({}) is True

    def test_env_adds_disable_gpu_once(self):
        env = startup.with_safe_render_env({"X": "1"})
        assert env[startup.WEBVIEW2_ARGS_ENV] == "--disable-gpu" and env[startup.SAFE_RENDER_ENV] == "1"
        again = startup.with_safe_render_env(env)
        assert again[startup.WEBVIEW2_ARGS_ENV] == "--disable-gpu"
        merged = startup.with_safe_render_env({startup.WEBVIEW2_ARGS_ENV: "--foo"})
        assert merged[startup.WEBVIEW2_ARGS_ENV] == "--foo --disable-gpu"


# ==================== ログ・スプラッシュ ====================

class TestLoggingAndSplash:
    def test_com_conflict_noted_once_as_normal_line(self, tmp_path, caplog):
        log = tmp_path / "a.log"
        log.write_text("x\nWindows fatal exception: code 0x8001010d\ny\n", encoding="utf-8")
        app_logging._state.pop("com_noted", None)
        with caplog.at_level("WARNING", logger="voice2doc"):
            assert app_logging.note_known_fatal_codes(log) is True
            assert app_logging.note_known_fatal_codes(log) is False  # 2回目は何もしない
        messages = [r.getMessage() for r in caplog.records if "0x8001010d" in r.getMessage()]
        assert len(messages) == 1 and "無害" in messages[0]
        app_logging._state.pop("com_noted", None)

    def test_no_note_without_the_code(self, tmp_path):
        log = tmp_path / "b.log"
        log.write_text("nothing\n", encoding="utf-8")
        app_logging._state.pop("com_noted", None)
        assert app_logging.note_known_fatal_codes(log) is False

    @pytest.mark.parametrize("lang,word", [("ja", "起動しています"), ("en", "Starting up")])
    def test_splash_languages(self, lang, word):
        page = startup.build_splash_html(lang)
        assert word in page and "retry" not in page

    def test_error_splash_has_cause_log_and_retry(self):
        page = startup.build_splash_html("en", "port <busy>", r"C:\logs")
        assert "port &lt;busy&gt;" in page  # エスケープされる
        assert r"C:\logs" in page and "pywebview.api.retry()" in page and "Try again" in page

    def test_startup_delay_switch(self, monkeypatch):
        assert startup.test_startup_delay() == 0.0
        monkeypatch.setenv(startup.TEST_STARTUP_DELAY_ENV, "2.5")
        assert startup.test_startup_delay() == 2.5
        monkeypatch.setenv(startup.TEST_STARTUP_DELAY_ENV, "abc")
        assert startup.test_startup_delay() == 0.0


# ==================== 8008 固定の記述が残っていないこと ====================

class TestNoHardCodedPort:
    def test_frontend_uses_relative_urls(self):
        for name in ("src/app.js", "src/wizard.js", "src/index.html", "src/wizard.html"):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert "8008" not in text and "127.0.0.1" not in text, name

    def test_main_py_has_no_literal_port(self):
        assert "8008" not in (ROOT / "main.py").read_text(encoding="utf-8")

    def test_installer_and_docs_do_not_pin_8008(self):
        """8008 を書くときは、使用中なら 8030 までの空きポートへ移ることも一緒に書く（固定だと誤解させない）"""
        assert "8008" not in (ROOT / "installer/voice2doc_installer.nsi").read_text(encoding="utf-8")
        for name in ("SECURITY.md", "README.md", "README.ja.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            if "8008" in text:
                assert "8030" in text, name

    def test_main_py_wires_the_recovery_flow(self):
        text = (ROOT / "main.py").read_text(encoding="utf-8")
        for needle in ("build_splash_html", "run_watchdog", "wait_for_server", "select_port",
                       "cleanup_stale_userdata", "configure_cors", "should_exit", "single_instance.release()"):
            assert needle in text or needle in (ROOT / "src_backends/startup.py").read_text(encoding="utf-8"), needle


class TestReviewFixes:
    """コードレビューの指摘の回帰テスト"""

    def test_health_reports_safe_render_when_started_in_safe_mode(self, test_client, monkeypatch):
        """自動の安全モード再起動の最初の起動でも、マーカーが無くても設定の注記を出せる"""
        monkeypatch.setenv(startup.SAFE_RENDER_ENV, "1")
        assert test_client.get("/health").json()["safe_render"] is True

    def test_relaunch_closes_without_confirmation_and_recovers_on_failure(self):
        main_py = (ROOT / "main.py").read_text(encoding="utf-8")
        assert "window.confirm_close = False" in main_py  # 自己再起動で「終了しますか」を出さない
        assert "window.events.closed += on_closed" in main_py  # 確認でキャンセルされても終了扱いにしない
        assert "single_instance.acquire()" in main_py  # 再起動に失敗したら二重起動の防止を戻す
        assert "_abort_if_closing()" in main_py and "_boot_done.wait" in main_py

    def test_early_error_panel_is_removed_once_booted(self):
        for name in ("src/app.js", "src/wizard.js"):
            assert "data-early-error" in (ROOT / name).read_text(encoding="utf-8"), name
        for name in ("src/index.html", "src/wizard.html"):
            html = (ROOT / name).read_text(encoding="utf-8")
            assert "t.tagName === 'SCRIPT'" in html, name  # 画像などの軽微な読込失敗では覆わない
