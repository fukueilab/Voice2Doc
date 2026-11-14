"""Unit tests for vibe_client.py (Vibeプロセス管理・文字起こしクライアント)"""
import json
from unittest.mock import MagicMock, patch

import pytest

from src_backends import vibe_client


@pytest.fixture(autouse=True)
def _reset_vibe_globals(monkeypatch):
    """モジュールグローバル(_vibe_process等)がテスト間で漏れないよう毎回リセットする"""
    monkeypatch.setattr(vibe_client, "_vibe_process", None)
    monkeypatch.setattr(vibe_client, "_vibe_port", None)
    monkeypatch.setattr(vibe_client, "_vibe_model_name", None)


class TestFindVibeBinary:
    def test_custom_path_exists_returns_it(self, tmp_path):
        custom = tmp_path / "custom_vibe-server.exe"
        custom.write_text("")
        assert vibe_client.find_vibe_binary(str(custom)) == custom

    def test_custom_path_missing_falls_back_to_candidates(self, tmp_path, monkeypatch):
        fallback = tmp_path / "fallback" / "vibe-server.exe"
        fallback.parent.mkdir()
        fallback.write_text("")
        monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [fallback])
        result = vibe_client.find_vibe_binary(str(tmp_path / "no_such.exe"))
        assert result == fallback

    def test_no_candidates_exist_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            vibe_client, "VIBE_BINARY_CANDIDATES", [tmp_path / "missing1.exe", tmp_path / "missing2.exe"]
        )
        # 実機にVibeがインストール済みだとレジストリ検出が本物の結果を返してしまうため無効化
        monkeypatch.setattr(vibe_client, "_find_vibe_install_dir_from_registry", lambda: None)
        assert vibe_client.find_vibe_binary() is None

    def test_returns_first_matching_candidate(self, tmp_path, monkeypatch):
        first = tmp_path / "first.exe"
        second = tmp_path / "second.exe"
        first.write_text("")
        second.write_text("")
        monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [first, second])
        assert vibe_client.find_vibe_binary() == first

    def test_falls_back_to_registry_when_no_fixed_candidate_matches(self, tmp_path, monkeypatch):
        """固定候補パスがどれも存在しない場合、レジストリ経由のVibeインストール先を試す"""
        vibe_dir = tmp_path / "vibe_install"
        vibe_dir.mkdir()
        vibe_server = vibe_dir / "vibe-server.exe"
        vibe_server.write_text("")

        monkeypatch.setattr(
            vibe_client, "VIBE_BINARY_CANDIDATES", [tmp_path / "missing1.exe", tmp_path / "missing2.exe"]
        )
        monkeypatch.setattr(vibe_client, "_find_vibe_install_dir_from_registry", lambda: vibe_dir)
        assert vibe_client.find_vibe_binary() == vibe_server

    def test_registry_dir_without_vibe_server_exe_returns_none(self, tmp_path, monkeypatch):
        vibe_dir = tmp_path / "vibe_install_empty"
        vibe_dir.mkdir()

        monkeypatch.setattr(
            vibe_client, "VIBE_BINARY_CANDIDATES", [tmp_path / "missing1.exe"]
        )
        monkeypatch.setattr(vibe_client, "_find_vibe_install_dir_from_registry", lambda: vibe_dir)
        assert vibe_client.find_vibe_binary() is None


class TestFindVibeInstallDirFromRegistry:
    def test_returns_install_location_when_key_exists(self, tmp_path, monkeypatch):
        import winreg

        fake_dir = tmp_path / "vibe"
        fake_dir.mkdir()

        fake_key = MagicMock()
        fake_key.__enter__.return_value = fake_key
        fake_key.__exit__.return_value = False
        monkeypatch.setattr(winreg, "OpenKey", MagicMock(return_value=fake_key))
        monkeypatch.setattr(winreg, "QueryValueEx", MagicMock(return_value=(f'"{fake_dir}"', 1)))

        result = vibe_client._find_vibe_install_dir_from_registry()
        assert result == fake_dir

    def test_returns_none_when_key_missing(self, monkeypatch):
        import winreg

        monkeypatch.setattr(winreg, "OpenKey", MagicMock(side_effect=FileNotFoundError()))
        assert vibe_client._find_vibe_install_dir_from_registry() is None

    def test_returns_none_when_install_location_dir_does_not_exist(self, tmp_path, monkeypatch):
        import winreg

        fake_key = MagicMock()
        fake_key.__enter__.return_value = fake_key
        fake_key.__exit__.return_value = False
        monkeypatch.setattr(winreg, "OpenKey", MagicMock(return_value=fake_key))
        monkeypatch.setattr(
            winreg, "QueryValueEx", MagicMock(return_value=(str(tmp_path / "does_not_exist"), 1))
        )

        assert vibe_client._find_vibe_install_dir_from_registry() is None


class TestIsVibeRunning:
    def test_none_process_returns_false(self):
        assert vibe_client.is_vibe_running() is False

    def test_alive_process_returns_true(self, monkeypatch):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 1234
        monkeypatch.setattr(vibe_client, "_vibe_process", proc)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5000)
        assert vibe_client.is_vibe_running() is True

    def test_terminated_process_returns_false_and_clears_state(self, monkeypatch):
        proc = MagicMock()
        proc.poll.return_value = 1
        monkeypatch.setattr(vibe_client, "_vibe_process", proc)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5000)
        assert vibe_client.is_vibe_running() is False
        assert vibe_client._vibe_process is None
        assert vibe_client._vibe_port is None


class TestGetVibePort:
    def test_returns_current_port(self, monkeypatch):
        monkeypatch.setattr(vibe_client, "_vibe_port", 4242)
        assert vibe_client.get_vibe_port() == 4242

    def test_returns_none_when_not_set(self):
        assert vibe_client.get_vibe_port() is None


class TestStartVibe:
    def test_already_running_returns_existing_port(self, monkeypatch):
        proc = MagicMock()
        proc.poll.return_value = None
        monkeypatch.setattr(vibe_client, "_vibe_process", proc)
        monkeypatch.setattr(vibe_client, "_vibe_port", 9999)
        assert vibe_client.start_vibe() == 9999

    def test_binary_not_found_raises(self, monkeypatch):
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": None)
        with pytest.raises(RuntimeError, match="vibe-server.exeが見つかりません"):
            vibe_client.start_vibe()

    def test_popen_oserror_raises_runtime_error(self, tmp_path, monkeypatch):
        fake_bin = tmp_path / "vibe-server.exe"
        fake_bin.write_text("")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)
        # モデルファイルがある状態にする（実機のモデルに頼らない。CI にはモデルが無い）
        fake_model = tmp_path / "ggml-large-v3-turbo.bin"
        fake_model.write_text("")
        monkeypatch.setattr(vibe_client, "find_model_file", lambda name, vibe_bin_dir=None: fake_model)
        with patch.object(vibe_client.subprocess, "Popen", side_effect=OSError("no such file")):
            with pytest.raises(RuntimeError, match="起動に失敗"):
                vibe_client.start_vibe()

    def test_successful_startup_returns_port(self, tmp_path, monkeypatch):
        fake_bin = tmp_path / "vibe-server.exe"
        fake_bin.write_text("")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)

        fake_proc = MagicMock()
        fake_proc.pid = 111
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.side_effect = [json.dumps({"status": "ready", "port": 51234}) + "\n"]
        fake_proc.stderr = iter([])

        with patch.object(vibe_client.subprocess, "Popen", return_value=fake_proc):
            port = vibe_client.start_vibe(model_name="")
        assert port == 51234
        assert vibe_client._vibe_port == 51234

    def test_popen_called_with_create_no_window_flag(self, tmp_path, monkeypatch):
        """黒いコンソールウィンドウが表示されないよう、creationflagsを渡していることを確認"""
        fake_bin = tmp_path / "vibe-server.exe"
        fake_bin.write_text("")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)

        fake_proc = MagicMock()
        fake_proc.pid = 111
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.side_effect = [json.dumps({"status": "ready", "port": 51234}) + "\n"]
        fake_proc.stderr = iter([])

        with patch.object(vibe_client.subprocess, "Popen", return_value=fake_proc) as mock_popen:
            vibe_client.start_vibe(model_name="")
        _, kwargs = mock_popen.call_args
        assert kwargs["creationflags"] == getattr(vibe_client.subprocess, "CREATE_NO_WINDOW", 0)

    def test_process_exits_before_ready_raises_with_stderr(self, tmp_path, monkeypatch):
        fake_bin = tmp_path / "vibe-server.exe"
        fake_bin.write_text("")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)

        fake_proc = MagicMock()
        fake_proc.pid = 111
        fake_proc.poll.return_value = 1
        fake_proc.returncode = 1
        fake_proc.stdout = iter([])
        fake_proc.stderr = iter(["致命的エラー\n"])

        with patch.object(vibe_client.subprocess, "Popen", return_value=fake_proc):
            with pytest.raises(RuntimeError, match="異常終了"):
                vibe_client.start_vibe(model_name="")

    def test_finds_model_file_in_candidate_dir(self, tmp_path, monkeypatch):
        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()
        fake_bin = fake_bin_dir / "vibe-server.exe"
        fake_bin.write_text("")
        model_file = fake_bin_dir / "ggml-turbo.bin"
        model_file.write_text("x" * 1024)
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)

        fake_proc = MagicMock()
        fake_proc.pid = 111
        fake_proc.poll.return_value = None
        fake_proc.stdout.readline.side_effect = [json.dumps({"status": "ready", "port": 1}) + "\n"]
        fake_proc.stderr = iter([])

        captured_cmd = {}

        def fake_popen(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            return fake_proc

        with patch.object(vibe_client.subprocess, "Popen", side_effect=fake_popen):
            vibe_client.start_vibe(model_name="turbo")

        assert str(model_file) in captured_cmd["cmd"]


class TestStopVibe:
    def test_no_process_is_noop(self):
        vibe_client.stop_vibe()  # should not raise

    def test_terminates_cleanly(self, monkeypatch):
        proc = MagicMock()
        monkeypatch.setattr(vibe_client, "_vibe_process", proc)
        monkeypatch.setattr(vibe_client, "_vibe_port", 1)
        monkeypatch.setattr(vibe_client, "_vibe_model_name", "turbo")
        vibe_client.stop_vibe()
        proc.terminate.assert_called_once()
        assert vibe_client._vibe_process is None
        assert vibe_client._vibe_port is None
        assert vibe_client._vibe_model_name is None

    def test_terminate_timeout_forces_kill(self, monkeypatch):
        import subprocess as subprocess_mod
        proc = MagicMock()
        proc.wait.side_effect = subprocess_mod.TimeoutExpired(cmd="vibe", timeout=10)
        monkeypatch.setattr(vibe_client, "_vibe_process", proc)
        vibe_client.stop_vibe()
        proc.kill.assert_called_once()


class TestEnsureModel:
    def test_require_server_and_not_running_returns_false(self, monkeypatch):
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: False)
        assert vibe_client.ensure_model() is False

    def test_binary_not_found_returns_false(self, monkeypatch):
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": None)
        assert vibe_client.ensure_model() is False

    def test_model_file_found_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        fake_bin = tmp_path / "vibe-server.exe"
        fake_bin.write_text("")
        model_file = tmp_path / "ggml-large-v3-turbo.bin"
        model_file.write_text("x")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)

        progress = []
        result = vibe_client.ensure_model(progress_callback=lambda msg: progress.append(msg))
        assert result is True
        assert vibe_client._vibe_model_name == "ggml-large-v3-turbo"
        assert any("準備完了" in m for m in progress)

    def test_model_file_missing_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        fake_bin = tmp_path / "vibe-server.exe"
        fake_bin.write_text("")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)
        # 環境変数依存の候補パスが実マシンの本物のVibeモデルを拾わないよう隔離する
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no_such_localappdata"))
        monkeypatch.setenv("APPDATA", str(tmp_path / "no_such_appdata"))
        monkeypatch.setattr(vibe_client.Path, "home", classmethod(lambda cls: tmp_path / "no_such_home"))
        monkeypatch.setattr(vibe_client.os.path, "expanduser", lambda p: str(tmp_path / "no_such_home"))

        progress = []
        result = vibe_client.ensure_model(progress_callback=lambda msg: progress.append(msg))
        assert result is False
        assert any("見つかりません" in m for m in progress)

    def test_require_server_false_skips_running_check(self, tmp_path, monkeypatch):
        called = {"is_running": False}
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: called.__setitem__("is_running", True) or False)
        fake_bin = tmp_path / "vibe-server.exe"
        fake_bin.write_text("")
        model_file = tmp_path / "ggml-large-v3-turbo.bin"
        model_file.write_text("x")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": fake_bin)

        result = vibe_client.ensure_model(require_server=False)
        assert result is True
        assert called["is_running"] is False


class TestTranscribe:
    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            vibe_client.transcribe(tmp_path / "no_such_file.wav")

    def test_restart_fails_raises_runtime_error(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.wav"
        audio.write_text("")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: False)
        monkeypatch.setattr(vibe_client, "stop_vibe", lambda: None)
        monkeypatch.setattr(vibe_client, "start_vibe", MagicMock(side_effect=RuntimeError("boom")))
        with pytest.raises(RuntimeError, match="再起動に失敗"):
            vibe_client.transcribe(audio)

    def test_restart_succeeds_but_still_not_running_raises(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.wav"
        audio.write_text("")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: False)
        monkeypatch.setattr(vibe_client, "stop_vibe", lambda: None)
        monkeypatch.setattr(vibe_client, "start_vibe", lambda **kw: 1234)
        with pytest.raises(RuntimeError, match="稼働していません"):
            vibe_client.transcribe(audio)

    def test_successful_transcription(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-wav-data")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5001)
        monkeypatch.setattr("src_backends.whisper_processor.get_audio_duration", lambda p: 12.5)

        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"text": "こんにちは"}

        with patch.object(vibe_client.httpx, "post", return_value=fake_response) as mock_post:
            result = vibe_client.transcribe(audio, language="ja")

        assert result == {"text": "こんにちは", "segments": [], "language": "ja"}
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "http://127.0.0.1:5001/v1/audio/transcriptions"

    def _prepare(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-wav-data")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5001)
        monkeypatch.setattr("src_backends.whisper_processor.get_audio_duration", lambda p: 12.5)
        return audio

    def test_japanese_sends_punctuated_prompt_hint(self, tmp_path, monkeypatch):
        audio = self._prepare(tmp_path, monkeypatch)
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"text": "こんにちは"}
        with patch.object(vibe_client.httpx, "post", return_value=fake_response) as mock_post:
            vibe_client.transcribe(audio, language="ja")
        data = mock_post.call_args.kwargs["data"]
        assert data["prompt"] == vibe_client.TRANSCRIBE_PROMPT_JA
        assert "。" in data["prompt"] and "、" in data["prompt"]

    def test_other_languages_do_not_send_prompt(self, tmp_path, monkeypatch):
        audio = self._prepare(tmp_path, monkeypatch)
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"text": "hello"}
        with patch.object(vibe_client.httpx, "post", return_value=fake_response) as mock_post:
            vibe_client.transcribe(audio, language="en")
        assert "prompt" not in mock_post.call_args.kwargs["data"]

    def test_prompt_rejected_retries_without_prompt(self, tmp_path, monkeypatch):
        """promptを受け付けないvibe-server(400)では、ヒント無しで1回だけやり直す"""
        audio = self._prepare(tmp_path, monkeypatch)
        bad = MagicMock()
        bad.status_code = 400
        bad.text = "unknown field prompt"
        ok = MagicMock()
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"text": "こんにちは"}
        first = MagicMock()
        first.raise_for_status.side_effect = vibe_client.httpx.HTTPStatusError(
            "bad", request=MagicMock(), response=bad)
        sent = []

        def fake_post(url, files=None, data=None, timeout=None):
            sent.append(dict(data))
            return first if len(sent) == 1 else ok

        with patch.object(vibe_client.httpx, "post", side_effect=fake_post):
            result = vibe_client.transcribe(audio, language="ja")
        assert result["text"] == "こんにちは"
        assert "prompt" in sent[0] and "prompt" not in sent[1]

    def test_echoed_prompt_text_is_removed_from_result(self, tmp_path, monkeypatch):
        audio = self._prepare(tmp_path, monkeypatch)
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"text": vibe_client.TRANSCRIBE_PROMPT_JA}
        with patch.object(vibe_client.httpx, "post", return_value=fake_response):
            result = vibe_client.transcribe(audio, language="ja")
        assert result["text"] == ""

    def test_double_newlines_between_segments_are_normalized(self, tmp_path, monkeypatch):
        """Vibeはセグメントごとに\\n\\nで区切って返すため、1つの改行に正規化されること"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-wav-data")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5001)
        monkeypatch.setattr("src_backends.whisper_processor.get_audio_duration", lambda p: 12.5)

        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "text": "\nこんにちは\n\n元気ですか\n\n\nさようなら\n"
        }

        with patch.object(vibe_client.httpx, "post", return_value=fake_response):
            result = vibe_client.transcribe(audio, language="ja")

        assert result["text"] == "こんにちは\n元気ですか\nさようなら"

    def test_video_file_extracts_audio_first(self, tmp_path, monkeypatch):
        video = tmp_path / "a.mp4"
        video.write_bytes(b"fake-video-data")
        extracted = tmp_path / "extracted.wav"
        extracted.write_bytes(b"fake-audio")

        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5001)
        monkeypatch.setattr("src_backends.whisper_processor.is_video_file", lambda p: True)
        monkeypatch.setattr("src_backends.whisper_processor.extract_audio_from_video", lambda p: extracted)
        monkeypatch.setattr("src_backends.whisper_processor.get_audio_duration", lambda p: 3.0)

        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"text": "video transcript"}

        with patch.object(vibe_client.httpx, "post", return_value=fake_response):
            result = vibe_client.transcribe(video)

        assert result["text"] == "video transcript"
        # 一時音声ファイルは処理後に削除される
        assert not extracted.exists()

    def test_timeout_raises_friendly_error(self, tmp_path, monkeypatch):
        import httpx as httpx_mod
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"data")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5001)
        monkeypatch.setattr("src_backends.whisper_processor.get_audio_duration", lambda p: 1.0)

        with patch.object(vibe_client.httpx, "post", side_effect=httpx_mod.TimeoutException("timeout")):
            with pytest.raises(RuntimeError, match="タイムアウト"):
                vibe_client.transcribe(audio)

    def test_rate_limit_429_raises_friendly_error(self, tmp_path, monkeypatch):
        import httpx as httpx_mod
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"data")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5001)
        monkeypatch.setattr("src_backends.whisper_processor.get_audio_duration", lambda p: 1.0)

        fake_request = MagicMock()
        fake_resp = MagicMock(status_code=429, text="rate limited")
        error = httpx_mod.HTTPStatusError("429", request=fake_request, response=fake_resp)
        with patch.object(vibe_client.httpx, "post", side_effect=error):
            with pytest.raises(RuntimeError, match="同時に複数"):
                vibe_client.transcribe(audio)

    def test_other_http_error_raises_with_details(self, tmp_path, monkeypatch):
        import httpx as httpx_mod
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"data")
        monkeypatch.setattr(vibe_client, "is_vibe_running", lambda: True)
        monkeypatch.setattr(vibe_client, "_vibe_port", 5001)
        monkeypatch.setattr("src_backends.whisper_processor.get_audio_duration", lambda p: 1.0)

        fake_request = MagicMock()
        fake_resp = MagicMock(status_code=500, text="internal error")
        error = httpx_mod.HTTPStatusError("500", request=fake_request, response=fake_resp)
        with patch.object(vibe_client.httpx, "post", side_effect=error):
            with pytest.raises(RuntimeError, match="vibe-server APIエラー"):
                vibe_client.transcribe(audio)


class TestVibeDetection:
    """vibe-server.exe・ffmpeg.exe は同梱せず、Vibe のものを使う"""

    def test_candidates_are_vibe_server_only(self):
        names = {p.name for p in vibe_client.VIBE_BINARY_CANDIDATES}
        assert names == {"vibe-server.exe"}
        assert vibe_client.VIBE_DIR / "vibe-server.exe" in vibe_client.VIBE_BINARY_CANDIDATES
        assert vibe_client.VIBE_PROGRAMS_DIR / "vibe-server.exe" in vibe_client.VIBE_BINARY_CANDIDATES

    def test_registry_install_dir_is_still_used(self, tmp_path, monkeypatch):
        (tmp_path / "vibe-server.exe").write_text("")
        monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [tmp_path / "missing.exe"])
        monkeypatch.setattr(vibe_client, "_find_vibe_install_dir_from_registry", lambda: tmp_path)
        assert vibe_client.find_vibe_binary() == tmp_path / "vibe-server.exe"

    def test_ffmpeg_next_to_vibe_server_is_preferred(self, tmp_path, monkeypatch):
        (tmp_path / "vibe-server.exe").write_text("")
        (tmp_path / "ffmpeg.exe").write_text("")
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: "C:/elsewhere/ffmpeg.exe")
        assert vibe_client.find_ffmpeg(str(tmp_path / "vibe-server.exe")) == tmp_path / "ffmpeg.exe"

    def test_ffmpeg_falls_back_to_path(self, tmp_path, monkeypatch):
        (tmp_path / "vibe-server.exe").write_text("")  # Vibe はあるが ffmpeg.exe が無い
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: str(tmp_path / "bin" / "ffmpeg.exe"))
        assert vibe_client.find_ffmpeg(str(tmp_path / "vibe-server.exe")) == tmp_path / "bin" / "ffmpeg.exe"

    def test_ffmpeg_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": None)
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: None)
        assert vibe_client.find_ffmpeg() is None

    def test_vibe_dir_is_added_to_path_once(self, tmp_path, monkeypatch):
        (tmp_path / "vibe-server.exe").write_text("")
        (tmp_path / "ffmpeg.exe").write_text("")
        monkeypatch.setenv("PATH", "C:\\Windows")
        binary = str(tmp_path / "vibe-server.exe")
        assert vibe_client.add_vibe_to_path(binary) is True
        assert vibe_client.add_vibe_to_path(binary) is True
        parts = vibe_client.os.environ["PATH"].split(vibe_client.os.pathsep)
        assert parts[0] == str(tmp_path.resolve()) and parts.count(str(tmp_path.resolve())) == 1

    def test_vibe_dir_without_ffmpeg_is_not_added(self, tmp_path, monkeypatch):
        (tmp_path / "vibe-server.exe").write_text("")
        monkeypatch.setenv("PATH", "C:\\Windows")
        assert vibe_client.add_vibe_to_path(str(tmp_path / "vibe-server.exe")) is False
        assert vibe_client.os.environ["PATH"] == "C:\\Windows"


def os_is_windows():
    import os
    return os.name == "nt"
