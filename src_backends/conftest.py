"""Pytest shared fixtures for Voice2Doc tests."""
import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src_backends modules are importable
_project_root = str(Path(__file__).resolve().parent.parent)
_src_backends = str(Path(__file__).resolve().parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _src_backends not in sys.path:
    sys.path.insert(0, _src_backends)


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect all config/history IO to tmp_path for every test."""
    from src_backends import app_logging, i18n
    from src_backends import config as cfg
    from src_backends import main as main_mod

    i18n.set_language("ja")  # 表示言語は毎回日本語から始める（既存テストの文言に合わせる）
    main_mod._lingering.clear()  # 他のテストで中止され、終了しないまま残ったタスクの記録を持ち越さない
    test_base = tmp_path / "Voice2Doc"
    test_base.mkdir(parents=True, exist_ok=True)
    input_dir = test_base / "input"
    output_dir = test_base / "output"
    cache_dir = test_base / ".cache"
    config_file = test_base / "config.json"
    history_file = test_base / "history.json"
    done_log_file = test_base / "done.log"

    for d in [input_dir, output_dir, cache_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 実機のVibe（HKLM・Program Files・スタートメニュー）を検出してしまわないよう、追加の検出場所は空にする
    from src_backends import vibe_client as _vc
    monkeypatch.setattr(_vc, "_find_vibe_install_dirs_from_hklm", lambda: [])
    monkeypatch.setattr(_vc, "_program_files_candidates", lambda: [])
    monkeypatch.setattr(_vc, "_start_menu_shortcut_targets", lambda: [])

    # vibe_client.add_vibe_to_path() が書き換える PATH をテストごとに元へ戻す
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setattr(cfg, "BASE_DIR", test_base)
    monkeypatch.setattr(cfg, "INPUT_DIR", input_dir)
    monkeypatch.setattr(cfg, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(cfg, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cfg, "CONFIG_FILE", config_file)
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_FILE", test_base / "default.json")
    monkeypatch.setattr(cfg, "DONE_LOG_FILE", done_log_file)
    # アクティビティログも実ユーザー領域へ書かないよう隔離する
    monkeypatch.setattr(app_logging, "LOG_DIR", test_base / "logs")
    # config.jsonはメモリキャッシュされる。前のテストのキャッシュが漏れないよう
    # このテストの開始時点でリセットする（各テストはCONFIG_FILEを上のtmp_pathに向け直しているため、
    # キャッシュが残ったままだと前のテストのAppConfigインスタンスが読み込まれてしまう）
    cfg.invalidate_config_cache()

    monkeypatch.setattr(main_mod, "INPUT_DIR", input_dir)
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(main_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr(main_mod, "DONE_LOG_FILE", done_log_file)

    return {
        "base": test_base,
        "input": input_dir,
        "output": output_dir,
        "cache": cache_dir,
        "config_file": config_file,
        "history_file": history_file,
        "done_log_file": done_log_file,
    }


@pytest.fixture
def sample_config_data():
    """Sample config dictionary for tests."""
    return {
        "whisper_backend": "vibe",
        "whisper_model": "turbo",
        "whisper_language": "ja",
        "vibe_server_path": "",
        "vibe_model": "large-v3-turbo",
        "dark_mode": False,
        "prompt": "Test prompt",
        "active_prompt_name": "daily",
        "saved_prompts": {
            "daily": {
                "content": "Test prompt",
                "description": "Auto-generate a weekly summary",
                "output_dir": "",
                "include_transcript": False,
            }
        },
        "max_files": 10,
        "max_total_size_bytes": 1024 * 1024 * 1024,
        "allowed_extensions": [
            ".wav", ".mp3", ".aac", ".m4a", ".ogg", ".flac",
            ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
            ".txt", ".md",
        ],
    }


@pytest.fixture
def write_config(isolate_config, sample_config_data):
    """Write a config.json and return its path."""
    from src_backends import config as cfg

    config_file = isolate_config["config_file"]

    def _write(data=None):
        payload = data or sample_config_data
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        # config.jsonをsave_config()を経由せず直接書き換えているため、次のload_config()が
        # 古いメモリキャッシュではなく今書いた内容を読むようキャッシュを破棄する
        cfg.invalidate_config_cache()
        return config_file

    return _write


@pytest.fixture
def sample_history_file(isolate_config):
    """Create a history.json and return a Path object."""
    history_file = isolate_config["history_file"]

    def _write(data=None):
        payload = data or {"hidden": []}
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return history_file

    return _write


@pytest.fixture
def test_client(isolate_config):
    """FastAPI TestClient with mocked external dependencies."""
    from starlette.testclient import TestClient
    from src_backends.main import app

    with patch("src_backends.main.whisper_transcribe") as mock_whisper, \
         patch("src_backends.main.get_model") as mock_model, \
         patch("src_backends.main.get_model_info") as mock_info, \
         patch("src_backends.main.vibe_client") as mock_vibe, \
         patch("src_backends.main.local_llm") as mock_llm, \
         patch("src_backends.main.setup_checker") as mock_setup_checker:

        mock_info.return_value = {"model": "turbo", "status": "ready"}
        mock_vibe.is_vibe_running.return_value = False
        mock_vibe.get_vibe_port.return_value = None
        mock_setup_checker.run_all_checks.return_value = {
            "vc_runtime": True, "webview2": True, "ffmpeg": True,
            "vibe": True, "disk_space": True, "gpu": {},
        }

        client = TestClient(app, raise_server_exceptions=False)
        yield client
