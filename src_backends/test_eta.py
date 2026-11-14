"""Unit tests for eta.py (音声認識の推定時間・AI生成の経過時間)"""
import json

import pytest

from src_backends import eta


class TestFormat:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "0:00"), (5, "0:05"), (65, "1:05"), (600, "10:00"), (3725, "1:02:05"),
    ])
    def test_format_elapsed(self, seconds, expected):
        assert eta.format_elapsed(seconds) == expected

    @pytest.mark.parametrize("seconds,expected", [
        (4.2, "4秒"), (59, "59秒"), (60, "1分0秒"), (125, "2分5秒"), (3725, "1時間2分"),
    ])
    def test_format_remaining(self, seconds, expected):
        assert eta.format_remaining(seconds) == expected


class TestPredict:
    def test_prediction_scales_with_duration_and_factor(self):
        assert eta.predict_recognition_seconds(600, 0.1, is_video=False, backend="vibe") == pytest.approx(60.0)

    def test_video_and_python_add_fixed_overhead(self):
        base = eta.predict_recognition_seconds(100, 0.2, is_video=False, backend="vibe")
        assert eta.predict_recognition_seconds(100, 0.2, is_video=True, backend="vibe") == pytest.approx(
            base + eta.OVERHEAD_FFMPEG_SEC)
        assert eta.predict_recognition_seconds(100, 0.2, is_video=False, backend="python") == pytest.approx(
            base + eta.OVERHEAD_MODEL_SEC)

    def test_zero_duration_is_minimum(self):
        assert eta.predict_recognition_seconds(0, 0.2, is_video=False, backend="vibe") >= eta.MIN_PREDICTION_SEC


class TestComputeEtaText:
    def _status(self, **kw):
        base = {"current_step": None, "step_started_at": None,
                "recognition_started_at": None, "recognition_predicted_sec": None}
        base.update(kw)
        return base

    def test_recognition_counts_down_from_prediction(self):
        s = self._status(current_step="transcribe", recognition_started_at=1000.0, recognition_predicted_sec=130.0)
        assert eta.compute_eta_text(s, now=1010.0) == "推定: 2分0秒"

    def test_recognition_estimate_updates_with_time_not_only_on_callbacks(self):
        s = self._status(current_step="transcribe", recognition_started_at=1000.0, recognition_predicted_sec=130.0)
        assert eta.compute_eta_text(s, now=1010.0) != eta.compute_eta_text(s, now=1100.0)

    def test_recognition_overrun_does_not_show_negative_or_frozen_estimate(self):
        s = self._status(current_step="transcribe", recognition_started_at=1000.0, recognition_predicted_sec=30.0)
        text = eta.compute_eta_text(s, now=1100.0)
        assert "推定" in text and "超" in text and "1:40" in text

    def test_ffmpeg_and_model_steps_use_recognition_estimate(self):
        for step in ("ffmpeg", "model"):
            s = self._status(current_step=step, recognition_started_at=1000.0, recognition_predicted_sec=100.0)
            assert eta.compute_eta_text(s, now=1010.0) == "推定: 1分30秒"

    def test_llm_step_shows_elapsed_not_estimate(self):
        s = self._status(current_step="llm", step_started_at=1000.0,
                         recognition_started_at=900.0, recognition_predicted_sec=10.0)
        text = eta.compute_eta_text(s, now=1083.0)
        assert text == "AI処理 経過時間: 1:23"
        assert "推定" not in text

    def test_no_data_returns_none(self):
        assert eta.compute_eta_text(self._status(current_step="transcribe"), now=1.0) is None
        assert eta.compute_eta_text(self._status(current_step="llm"), now=1.0) is None
        assert eta.compute_eta_text(self._status(current_step=None), now=1.0) is None
        assert eta.compute_eta_text(self._status(current_step="llm_download"), now=1.0) is None


class TestLearnedFactor:
    def test_defaults_when_no_stats(self, isolate_config):
        assert eta.load_factor("vibe") == eta.DEFAULT_FACTORS["vibe"]
        assert eta.load_factor("python") == eta.DEFAULT_FACTORS["python"]
        assert eta.load_factor("unknown-backend") == eta.DEFAULT_FACTORS["vibe"]

    def test_record_run_learns_and_persists(self, isolate_config):
        # 基準オーバーヘッドを除いた 実測/音声長 = (70-0)/700 = 0.1
        eta.record_run("vibe", elapsed_sec=70.0, audio_duration=700.0, is_video=False)
        assert eta.load_factor("vibe") != eta.DEFAULT_FACTORS["vibe"]
        data = json.loads((isolate_config["cache"] / "eta_stats.json").read_text(encoding="utf-8"))
        assert "vibe" in data

    def test_ema_moves_toward_new_observation(self, isolate_config):
        eta.record_run("vibe", 100.0, 1000.0, False)   # 0.1
        first = eta.load_factor("vibe")
        eta.record_run("vibe", 100.0, 1000.0, False)
        second = eta.load_factor("vibe")
        target = 0.1
        assert abs(second - target) <= abs(first - target)

    def test_short_audio_is_ignored(self, isolate_config):
        eta.record_run("vibe", 3.0, 2.0, False)
        assert eta.load_factor("vibe") == eta.DEFAULT_FACTORS["vibe"]

    def test_absurd_values_are_clamped(self, isolate_config):
        eta.record_run("vibe", 100000.0, 10.0, False)
        assert eta.load_factor("vibe") <= eta.MAX_FACTOR

    def test_corrupt_stats_file_falls_back_to_default(self, isolate_config):
        (isolate_config["cache"] / "eta_stats.json").write_text("{not json", encoding="utf-8")
        assert eta.load_factor("vibe") == eta.DEFAULT_FACTORS["vibe"]
        # 壊れていても記録できる(上書き)
        eta.record_run("vibe", 100.0, 1000.0, False)
        assert eta.load_factor("vibe") != eta.DEFAULT_FACTORS["vibe"]

    def test_unwritable_cache_dir_does_not_raise(self, isolate_config, monkeypatch):
        blocker = isolate_config["base"] / "blocker"
        blocker.write_text("x")
        from src_backends import config as cfg
        monkeypatch.setattr(cfg, "CACHE_DIR", blocker / "sub")
        eta.record_run("vibe", 100.0, 1000.0, False)  # 例外にならない


class TestTranscribeOnly:
    def test_transcribe_only_does_not_subtract_overhead(self, isolate_config):
        eta.record_run("python", 100.0, 1000.0, True, transcribe_only=True)
        # 100/1000=0.1 を観測値とし、初期値0.4とのEMA=0.25（オーバーヘッドを引かない）
        assert eta.load_factor("python") == pytest.approx(0.25, abs=1e-3)

    def test_default_subtracts_overhead(self, isolate_config):
        eta.record_run("python", 100.0, 1000.0, True)
        # (100-5-3)/1000=0.092 → 0.5*0.092+0.5*0.4=0.246
        assert eta.load_factor("python") == pytest.approx(0.246, abs=1e-3)
