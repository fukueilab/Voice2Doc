"""Unit tests for whisper_processor.py (Python Whisperローカル実行バックエンド)"""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src_backends import whisper_processor


@pytest.fixture(autouse=True)
def _reset_model_cache(monkeypatch):
    monkeypatch.setattr(whisper_processor, "_loaded_model", None)
    monkeypatch.setattr(whisper_processor, "_loaded_model_name", None)


class TestCheckModelExists:
    def test_returns_false_for_unknown_model(self):
        assert whisper_processor.check_model_exists("not-a-real-model") is False

    def test_returns_true_when_found_in_cache_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(whisper_processor, "CACHE_DIR", tmp_path)
        (tmp_path / "turbo.pt").write_text("x")
        assert whisper_processor.check_model_exists("turbo") is True

    def test_returns_false_when_not_found_anywhere(self, tmp_path, monkeypatch):
        monkeypatch.setattr(whisper_processor, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(whisper_processor.Path, "home", classmethod(lambda cls: tmp_path / "no_such_home"))
        assert whisper_processor.check_model_exists("turbo") is False

    def test_returns_true_when_found_in_default_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(whisper_processor, "CACHE_DIR", tmp_path / "empty")
        (tmp_path / "empty").mkdir()
        default_cache = tmp_path / "home" / ".cache" / "whisper"
        default_cache.mkdir(parents=True)
        (default_cache / "base.pt").write_text("x")
        monkeypatch.setattr(whisper_processor.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        assert whisper_processor.check_model_exists("base") is True


class TestGetModel:
    def test_returns_cached_model_when_name_matches(self, monkeypatch):
        sentinel = MagicMock()
        monkeypatch.setattr(whisper_processor, "_loaded_model", sentinel)
        monkeypatch.setattr(whisper_processor, "_loaded_model_name", "turbo")
        with patch.object(whisper_processor.whisper, "load_model") as mock_load:
            result = whisper_processor.get_model("turbo")
        assert result is sentinel
        mock_load.assert_not_called()

    def test_raises_for_unsupported_model(self):
        with pytest.raises(ValueError, match="サポートされていない"):
            whisper_processor.get_model("not-a-model")

    def test_loads_and_caches_model(self, monkeypatch):
        sentinel = MagicMock()
        monkeypatch.setattr(whisper_processor, "check_model_exists", lambda name: False)
        with patch.object(whisper_processor.whisper, "load_model", return_value=sentinel) as mock_load:
            result = whisper_processor.get_model("tiny")
        assert result is sentinel
        assert whisper_processor._loaded_model_name == "tiny"
        mock_load.assert_called_once()

    def test_concurrent_calls_load_model_only_once(self, monkeypatch):
        """起動時準備とリクエスト処理のexecutorを分離しているため、起動時のモデル準備と
        最初の/processリクエストが真に並行してget_model()を呼びうるようになったため、
        _model_load_lockが二重ロード（whisper.load_model()の同時実行）を防いでいることを確認する。
        ロックが機能していれば、後発スレッドは先発スレッドの完了を待ってからキャッシュヒットするため、
        load_model()自体は必ず1回しか呼ばれない"""
        import threading
        import time as time_mod

        monkeypatch.setattr(whisper_processor, "check_model_exists", lambda name: False)

        load_calls = []
        started = threading.Event()

        def slow_load_model(name, download_root=None):
            load_calls.append(name)
            started.set()
            time_mod.sleep(0.2)  # ロック保持中に後発スレッドが追いつく時間を与える
            return MagicMock()

        with patch.object(whisper_processor.whisper, "load_model", side_effect=slow_load_model):
            results = []
            errors = []

            def worker():
                try:
                    results.append(whisper_processor.get_model("tiny"))
                except Exception as e:
                    errors.append(e)

            first = threading.Thread(target=worker)
            first.start()
            started.wait(timeout=5)  # 1本目がload_model()に入るのを待ってから2本目を開始する
            second = threading.Thread(target=worker)
            second.start()
            first.join(timeout=5)
            second.join(timeout=5)

        assert errors == []
        assert len(load_calls) == 1  # ロックにより1回しかload_model()が呼ばれないこと
        assert results[0] is results[1]  # 両スレッドが同じインスタンスを受け取ること


class TestIsVideoFile:
    @pytest.mark.parametrize("filename", ["a.mp4", "a.MOV", "a.mkv", "a.webm"])
    def test_video_extensions_detected(self, filename):
        assert whisper_processor.is_video_file(filename) is True

    @pytest.mark.parametrize("filename", ["a.wav", "a.mp3", "a.txt"])
    def test_non_video_extensions_rejected(self, filename):
        assert whisper_processor.is_video_file(filename) is False


class TestGetAudioDuration:
    def test_returns_duration_on_success(self):
        fake_result = MagicMock(stdout="12.345\n")
        with patch.object(whisper_processor.subprocess, "run", return_value=fake_result):
            assert whisper_processor.get_audio_duration("a.wav") == 12.345

    def test_returns_zero_on_called_process_error(self):
        with patch.object(
            whisper_processor.subprocess, "run",
            side_effect=subprocess.CalledProcessError(1, "ffprobe"),
        ):
            assert whisper_processor.get_audio_duration("a.wav") == 0.0

    def test_returns_zero_on_unparseable_output(self):
        fake_result = MagicMock(stdout="not-a-number\n")
        with patch.object(whisper_processor.subprocess, "run", return_value=fake_result):
            assert whisper_processor.get_audio_duration("a.wav") == 0.0

    def test_run_called_with_create_no_window_flag(self):
        """黒いコンソールウィンドウが表示されないよう、creationflagsを渡していることを確認"""
        fake_result = MagicMock(stdout="1.0\n")
        with patch.object(whisper_processor.subprocess, "run", return_value=fake_result) as mock_run:
            whisper_processor.get_audio_duration("a.wav")
        _, kwargs = mock_run.call_args
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def test_falls_back_to_ffmpeg_when_ffprobe_is_missing(self, monkeypatch):
        """Vibe には ffprobe が無いため、ffmpeg -i の Duration 行から長さを求める"""
        monkeypatch.setattr(whisper_processor, "ffmpeg_command", lambda: "C:/vibe/ffmpeg.exe")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "ffprobe":
                raise FileNotFoundError()
            assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
            return MagicMock(returncode=1, stderr="Input #0, wav\n  Duration: 01:02:03.50, bitrate: 256 kb/s\n")

        with patch.object(whisper_processor.subprocess, "run", side_effect=fake_run):
            assert whisper_processor.get_audio_duration("a.wav") == 3723.5
        assert calls[1][0] == "C:/vibe/ffmpeg.exe" and calls[1][-1] == "a.wav"

    def test_both_calls_have_a_timeout_and_a_stalled_ffmpeg_gives_zero(self, monkeypatch):
        """壊れたファイル等で止まっても処理全体を待たせない（TimeoutExpired は 0 秒扱い）"""
        monkeypatch.setattr(whisper_processor, "ffmpeg_command", lambda: "ffmpeg")
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append((kwargs.get("timeout"), kwargs.get("stdin")))
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        with patch.object(whisper_processor.subprocess, "run", side_effect=fake_run):
            assert whisper_processor.get_audio_duration("a.wav") == 0.0
        assert seen == [(whisper_processor.DURATION_TIMEOUT_SEC, subprocess.DEVNULL)] * 2

    def test_zero_when_neither_ffprobe_nor_ffmpeg_exists(self, monkeypatch):
        monkeypatch.setattr(whisper_processor, "ffmpeg_command", lambda: "ffmpeg")
        with patch.object(whisper_processor.subprocess, "run", side_effect=FileNotFoundError()):
            assert whisper_processor.get_audio_duration("a.wav") == 0.0

    def test_zero_when_ffmpeg_output_has_no_duration(self, monkeypatch):
        monkeypatch.setattr(whisper_processor, "ffmpeg_command", lambda: "ffmpeg")

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                raise FileNotFoundError()
            return MagicMock(returncode=1, stderr="a.wav: Invalid data found when processing input")

        with patch.object(whisper_processor.subprocess, "run", side_effect=fake_run):
            assert whisper_processor.get_audio_duration("a.wav") == 0.0


class TestFfmpegCommand:
    def test_uses_ffmpeg_from_vibe_folder(self, tmp_path, monkeypatch):
        from src_backends import vibe_client
        monkeypatch.setattr(vibe_client, "find_ffmpeg", lambda binary_path="": tmp_path / "ffmpeg.exe")
        assert whisper_processor.ffmpeg_command() == str(tmp_path / "ffmpeg.exe")

    def test_falls_back_to_plain_name(self, monkeypatch):
        from src_backends import vibe_client
        monkeypatch.setattr(vibe_client, "find_ffmpeg", lambda binary_path="": None)
        assert whisper_processor.ffmpeg_command() == "ffmpeg"

    def test_extract_uses_resolved_ffmpeg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(whisper_processor, "ffmpeg_command", lambda: "C:/vibe/ffmpeg.exe")
        video = tmp_path / "a.mp4"
        video.write_bytes(b"fake")
        output = tmp_path / "a_audio.wav"

        def fake_run(cmd, **kwargs):
            assert cmd[0] == "C:/vibe/ffmpeg.exe"
            output.write_bytes(b"audio")
            return MagicMock()

        with patch.object(whisper_processor.subprocess, "run", side_effect=fake_run):
            assert whisper_processor.extract_audio_from_video(video, output) == output


class TestExtractAudioFromVideo:
    def test_success_returns_output_path(self, tmp_path):
        video = tmp_path / "a.mp4"
        video.write_bytes(b"fake")
        output = tmp_path / "a_audio.wav"

        def fake_run(cmd, **kwargs):
            output.write_bytes(b"audio-bytes")
            return MagicMock()

        with patch.object(whisper_processor.subprocess, "run", side_effect=fake_run):
            result = whisper_processor.extract_audio_from_video(video, output)
        assert result == output
        assert output.exists()

    def test_run_called_with_create_no_window_flag(self, tmp_path):
        """黒いコンソールウィンドウが表示されないよう、creationflagsを渡していることを確認"""
        video = tmp_path / "a.mp4"
        video.write_bytes(b"fake")
        output = tmp_path / "a_audio.wav"

        def fake_run(cmd, **kwargs):
            output.write_bytes(b"audio-bytes")
            assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
            return MagicMock()

        with patch.object(whisper_processor.subprocess, "run", side_effect=fake_run):
            whisper_processor.extract_audio_from_video(video, output)

    def test_ffmpeg_error_raises_runtime_error(self, tmp_path):
        video = tmp_path / "a.mp4"
        video.write_bytes(b"fake")
        error = subprocess.CalledProcessError(1, "ffmpeg", stderr="decode error")
        with patch.object(whisper_processor.subprocess, "run", side_effect=error):
            with pytest.raises(RuntimeError, match="音声抽出に失敗"):
                whisper_processor.extract_audio_from_video(video, tmp_path / "out.wav")

    def test_ffmpeg_not_found_raises_friendly_error(self, tmp_path):
        video = tmp_path / "a.mp4"
        video.write_bytes(b"fake")
        with patch.object(whisper_processor.subprocess, "run", side_effect=FileNotFoundError()):
            with pytest.raises(RuntimeError, match="FFmpegが見つかりません"):
                whisper_processor.extract_audio_from_video(video, tmp_path / "out.wav")

    def test_default_output_path_uses_tempdir(self, tmp_path, monkeypatch):
        video = tmp_path / "myrecording.mp4"
        video.write_bytes(b"fake")
        monkeypatch.setattr(whisper_processor.tempfile, "gettempdir", lambda: str(tmp_path))

        def fake_run(cmd, **kwargs):
            (tmp_path / "myrecording_audio.wav").write_bytes(b"x")
            return MagicMock()

        with patch.object(whisper_processor.subprocess, "run", side_effect=fake_run):
            result = whisper_processor.extract_audio_from_video(video)
        assert result == tmp_path / "myrecording_audio.wav"


class TestTranscribe:
    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            whisper_processor.transcribe(tmp_path / "missing.wav")

    def test_vibe_backend_delegates_to_vibe_client(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.wav"
        audio.write_text("")
        fake_transcribe = MagicMock(return_value={"text": "vibe result", "segments": [], "language": "ja"})
        with patch("src_backends.vibe_client.transcribe", fake_transcribe):
            result = whisper_processor.transcribe(audio, backend="vibe")
        assert result["text"] == "vibe result"
        fake_transcribe.assert_called_once()

    def test_python_backend_success(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.wav"
        audio.write_text("")
        monkeypatch.setattr(whisper_processor, "get_audio_duration", lambda p: 5.0)
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {
            "text": "こんにちは世界",
            "segments": [{"start": 0.0, "end": 1.0, "text": "こんにちは"}],
            "language": "ja",
        }
        monkeypatch.setattr(whisper_processor, "get_model", lambda name: fake_model)

        progress_events = []
        result = whisper_processor.transcribe(
            audio, progress_callback=lambda step, msg, pct=None: progress_events.append((step, msg))
        )
        assert result["text"] == "こんにちは世界"
        assert result["segments"] == [{"start": 0.0, "end": 1.0, "text": "こんにちは"}]
        assert result["language"] == "ja"
        assert ("transcribe", "音声認識完了") in progress_events

    def test_video_file_extracts_audio_and_cleans_up(self, tmp_path, monkeypatch):
        video = tmp_path / "a.mp4"
        video.write_text("")
        extracted = tmp_path / "extracted.wav"
        extracted.write_bytes(b"x")

        monkeypatch.setattr(whisper_processor, "extract_audio_from_video", lambda p: extracted)
        monkeypatch.setattr(whisper_processor, "get_audio_duration", lambda p: 2.0)
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": "video text", "segments": [], "language": "ja"}
        monkeypatch.setattr(whisper_processor, "get_model", lambda name: fake_model)

        result = whisper_processor.transcribe(video)
        assert result["text"] == "video text"
        assert not extracted.exists()


class TestGetModelInfo:
    def test_returns_current_state(self, monkeypatch):
        monkeypatch.setattr(whisper_processor, "_loaded_model_name", "turbo")
        info = whisper_processor.get_model_info()
        assert info["loaded_model"] == "turbo"
        assert info["available_models"] == whisper_processor.AVAILABLE_MODELS
