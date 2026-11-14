"""Unit tests for app_logging.py (起動ごとのアクティビティログ)"""
import logging
import re
import sys
from datetime import datetime

import pytest

from src_backends import app_logging

LOG_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.log$")


@pytest.fixture(autouse=True)
def _cleanup_logging():
    """setup_logging が root に付けたハンドラ等をテスト後に必ず外す"""
    yield
    app_logging.shutdown_logging()


class TestBuildLogPath:
    def test_filename_format(self, tmp_path):
        path = app_logging.build_log_path(tmp_path, now=datetime(2026, 10, 28, 21, 22, 31))
        assert path.name == "2026-10-28T21-22-31.log"
        assert path.parent == tmp_path

    def test_same_second_gets_suffix(self, tmp_path):
        now = datetime(2026, 10, 28, 21, 22, 31)
        (tmp_path / "2026-10-28T21-22-31.log").write_text("x")
        path = app_logging.build_log_path(tmp_path, now=now)
        assert path.name == "2026-10-28T21-22-31_1.log"


class TestPruneOldLogs:
    def test_keeps_latest_n_only(self, tmp_path):
        for day in range(1, 6):
            (tmp_path / f"2026-10-0{day}T00-00-00.log").write_text("x")
        app_logging.prune_old_logs(tmp_path, keep=2)
        remaining = sorted(p.name for p in tmp_path.glob("*.log"))
        assert remaining == ["2026-10-04T00-00-00.log", "2026-10-05T00-00-00.log"]

    def test_ignores_files_not_matching_pattern(self, tmp_path):
        (tmp_path / "notes.txt").write_text("keep me")
        (tmp_path / "2026-10-01T00-00-00.log").write_text("x")
        app_logging.prune_old_logs(tmp_path, keep=0)
        assert (tmp_path / "notes.txt").exists()
        assert not (tmp_path / "2026-10-01T00-00-00.log").exists()

    def test_missing_dir_is_noop(self, tmp_path):
        app_logging.prune_old_logs(tmp_path / "nope", keep=1)


class TestSetupLogging:
    def test_creates_log_file_and_writes_japanese_utf8(self, tmp_path):
        path = app_logging.setup_logging(
            log_dir=tmp_path, install_hooks=False, now=datetime(2026, 10, 28, 21, 22, 31)
        )
        assert path is not None
        assert LOG_NAME_RE.match(path.name)
        logging.getLogger("voice2doc.test").info("日本語のログ: 文字起こし開始")
        for h in logging.getLogger().handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
        assert "日本語のログ: 文字起こし開始" in text
        assert "voice2doc.test" in text
        assert "INFO" in text

    def test_idempotent_returns_same_path_without_duplicate_handlers(self, tmp_path):
        first = app_logging.setup_logging(log_dir=tmp_path, install_hooks=False)
        handlers_before = len(logging.getLogger().handlers)
        second = app_logging.setup_logging(log_dir=tmp_path, install_hooks=False)
        assert first == second
        assert len(logging.getLogger().handlers) == handlers_before
        assert len(list(tmp_path.glob("*.log"))) == 1

    def test_prunes_old_logs_on_startup(self, tmp_path):
        for day in range(1, 6):
            (tmp_path / f"2026-09-0{day}T00-00-00.log").write_text("x")
        app_logging.setup_logging(log_dir=tmp_path, install_hooks=False, keep=3)
        names = sorted(p.name for p in tmp_path.glob("*.log"))
        assert len(names) == 3  # 古い2件が消え、新規ログを含めて3件

    def test_none_std_streams_are_connected_to_log_file(self, tmp_path, monkeypatch):
        """console=False のexeでは sys.stdout/stderr が None。参照しても落ちないようにする"""
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        path = app_logging.setup_logging(log_dir=tmp_path, install_hooks=False)
        assert path is not None
        assert sys.stdout is not None and sys.stderr is not None
        assert sys.stdout.isatty() is False  # uvicornが isatty() を呼んでも安全
        # root に StreamHandler(stderr) が付かず、ファイルハンドラのみ
        stream_handlers = [
            h for h in logging.getLogger().handlers
            if type(h) is logging.StreamHandler
        ]
        assert stream_handlers == []

    def test_unwritable_dir_does_not_raise(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        # ディレクトリを作れない場所を指定してもアプリ起動を妨げない
        assert app_logging.setup_logging(log_dir=blocker / "logs", install_hooks=False) is None


class TestPollingAccessFilter:
    def test_status_and_health_polling_is_not_logged_but_other_requests_are(self, tmp_path):
        path = app_logging.setup_logging(log_dir=tmp_path, install_hooks=False)
        access = logging.getLogger("uvicorn.access")
        access.info('127.0.0.1:1 - "GET /status HTTP/1.1" 200')
        access.info('127.0.0.1:1 - "GET /health HTTP/1.1" 200')
        access.info('127.0.0.1:1 - "POST /process HTTP/1.1" 200')
        access.info('127.0.0.1:1 - "GET /status HTTP/1.1" 500')
        for h in logging.getLogger().handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
        assert "GET /status HTTP/1.1\" 200" not in text
        assert "GET /health" not in text
        assert "POST /process" in text
        assert "GET /status HTTP/1.1\" 500" in text  # 異常は残す


class TestPywebviewHandler:
    def test_pywebview_stream_handler_is_removed_so_logs_reach_file_once(self, tmp_path):
        """pywebviewがimport時に付けるStreamHandler(exeではstream=None)を外してrootへ流す"""
        pw = logging.getLogger("pywebview")
        bad = logging.StreamHandler()
        bad.stream = None
        pw.addHandler(bad)
        try:
            path = app_logging.setup_logging(log_dir=tmp_path, install_hooks=False)
            assert bad not in pw.handlers
            pw.info("pywebview-message")
            for h in logging.getLogger().handlers:
                h.flush()
            text = path.read_text(encoding="utf-8")
            assert text.count("pywebview-message") == 1
            assert "Logging error" not in text
        finally:
            pw.removeHandler(bad)


class TestUnhandledExceptionLogging:
    def test_log_unhandled_exception_records_traceback(self, tmp_path):
        path = app_logging.setup_logging(log_dir=tmp_path, install_hooks=False)
        try:
            raise ValueError("boom 失敗")
        except ValueError:
            app_logging.log_unhandled_exception(*sys.exc_info())
        for h in logging.getLogger().handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
        assert "未処理の例外" in text
        assert "ValueError: boom 失敗" in text
        assert "Traceback" in text
