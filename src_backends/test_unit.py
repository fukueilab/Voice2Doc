"""Unit tests for Voice2Doc utility functions and config."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src_backends import setup_checker
from src_backends.config import (
    AppConfig,
    load_config,
    load_default_config,
    reset_config,
    save_config,
    save_default_config,
    update_config,
)
from src_backends.main import (
    STEP_WEIGHT_TRANSCRIBE_MIN,
    _build_weighted_steps,
    _sanitize_title_for_filename,
    extract_h1_title,
    get_hidden_timestamp,
    get_scan_dirs,
    get_unique_path,
    is_file_hidden,
    is_text_file,
    load_history,
    save_history,
)
from src_backends.setup_checker import (
    VC_RUNTIME_DLLS,
    _download_and_verify,
    check_vc_runtime,
    install_vc_runtime,
    install_webview2,
)


class TestGetUniquePath:
    def test_returns_original_when_not_exists(self, tmp_path):
        result = get_unique_path(tmp_path, "report", ".md")
        assert result == tmp_path / "report.md"

    def test_appends_counter_when_exists(self, tmp_path):
        (tmp_path / "report.md").write_text("x")
        result = get_unique_path(tmp_path, "report", ".md")
        assert result == tmp_path / "report_1.md"

    def test_increments_counter(self, tmp_path):
        (tmp_path / "report.md").write_text("x")
        (tmp_path / "report_1.md").write_text("x")
        (tmp_path / "report_2.md").write_text("x")
        result = get_unique_path(tmp_path, "report", ".md")
        assert result == tmp_path / "report_3.md"


class TestIsTextFile:
    def test_txt(self):
        assert is_text_file("memo.txt") is True

    def test_md(self):
        assert is_text_file("readme.md") is True

    def test_wav(self):
        assert is_text_file("audio.wav") is False

    def test_path_object(self):
        assert is_text_file(Path("notes.md")) is True


class TestExtractH1Title:
    def test_h1_present(self):
        report = "# Weekly Summary\nSome content"
        assert extract_h1_title(report) == "Weekly Summary"

    def test_h2_fallback(self):
        report = "## Summary\nDetails"
        assert extract_h1_title(report) == "Summary"

    def test_h3_fallback(self):
        report = "### Notes\nDetails"
        assert extract_h1_title(report) == "Notes"

    def test_empty_string(self):
        assert extract_h1_title("") is None

    def test_none(self):
        assert extract_h1_title(None) is None


class TestSanitizeTitleForFilename:
    def test_normal_title_unchanged(self):
        assert _sanitize_title_for_filename("報告書") == "報告書"

    def test_reserved_chars_replaced(self):
        assert _sanitize_title_for_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"

    def test_long_title_truncated(self):
        title = "あ" * 100
        result = _sanitize_title_for_filename(title, max_len=40)
        assert len(result) == 40

    def test_dots_only_returns_empty(self):
        assert _sanitize_title_for_filename("...") == ""


class TestBuildWeightedSteps:
    def test_video_with_python_backend_has_all_four_steps(self):
        steps = _build_weighted_steps(is_video=True, whisper_backend="python", audio_duration=60.0)
        assert [name for name, _ in steps] == ["ffmpeg", "model", "transcribe", "llm"]

    def test_audio_with_vibe_backend_excludes_ffmpeg_and_model(self):
        steps = _build_weighted_steps(is_video=False, whisper_backend="vibe", audio_duration=60.0)
        assert [name for name, _ in steps] == ["transcribe", "llm"]

    def test_audio_with_python_backend_includes_model_not_ffmpeg(self):
        steps = _build_weighted_steps(is_video=False, whisper_backend="python", audio_duration=60.0)
        assert [name for name, _ in steps] == ["model", "transcribe", "llm"]

    def test_longer_audio_gives_larger_transcribe_weight(self):
        short_steps = dict(_build_weighted_steps(is_video=False, whisper_backend="vibe", audio_duration=60.0))
        long_steps = dict(_build_weighted_steps(is_video=False, whisper_backend="vibe", audio_duration=600.0))
        assert long_steps["transcribe"] > short_steps["transcribe"]

    def test_zero_audio_duration_falls_back_to_minimum_weight(self):
        steps = dict(_build_weighted_steps(is_video=False, whisper_backend="vibe", audio_duration=0.0))
        assert steps["transcribe"] == STEP_WEIGHT_TRANSCRIBE_MIN


class TestIsFileHidden:
    def test_in_hidden_list(self):
        hidden = [{"id": "dir::file.md", "hidden_at": 1.0}]
        assert is_file_hidden("dir::file.md", hidden) is True

    def test_not_in_hidden_list(self):
        hidden = [{"id": "dir::file.md", "hidden_at": 1.0}]
        assert is_file_hidden("dir::other.md", hidden) is False

    def test_string_format(self):
        hidden = ["dir::file.md"]
        assert is_file_hidden("dir::file.md", hidden) is True

    def test_empty_list(self):
        assert is_file_hidden("dir::file.md", []) is False


class TestGetHiddenTimestamp:
    def test_returns_timestamp(self):
        hidden = [{"id": "dir::file.md", "hidden_at": 123.45}]
        assert get_hidden_timestamp("dir::file.md", hidden) == 123.45

    def test_string_format_returns_zero(self):
        hidden = ["dir::file.md"]
        assert get_hidden_timestamp("dir::file.md", hidden) == 0.0

    def test_not_found_returns_zero(self):
        assert get_hidden_timestamp("dir::file.md", []) == 0.0


class TestHistory:
    def test_load_history_empty(self, sample_history_file):
        sample_history_file()
        data = load_history()
        assert data == {"hidden": []}

    def test_load_history_with_data(self, sample_history_file):
        sample_history_file({"hidden": [{"id": "file1.md", "hidden_at": 1.0}]})
        data = load_history()
        assert data["hidden"][0]["id"] == "file1.md"

    def test_load_history_nonexistent(self, isolate_config):
        data = load_history()
        assert data == {"hidden": []}

    def test_save_and_load_history(self, isolate_config):
        data = {"hidden": [{"id": "test::f.md", "hidden_at": 99.0}]}
        save_history(data)
        loaded = load_history()
        assert loaded["hidden"][0]["id"] == "test::f.md"


class TestGetScanDirs:
    def test_includes_output_dir(self, isolate_config):
        config = load_config()
        dirs = get_scan_dirs(config)
        assert isolate_config["output"] in dirs

    def test_includes_custom_output_dir(self, isolate_config):
        custom = isolate_config["base"] / "custom_out"
        custom.mkdir()
        config = load_config()
        config.saved_prompts = {
            "test": {"content": "x", "output_dir": str(custom), "description": "", "include_transcript": False}
        }
        dirs = get_scan_dirs(config)
        assert custom in dirs

    def test_no_duplicate_dirs(self, isolate_config):
        config = load_config()
        config.saved_prompts = {
            "a": {"content": "x", "output_dir": str(isolate_config["output"]), "description": "", "include_transcript": False}
        }
        dirs = get_scan_dirs(config)
        assert dirs.count(isolate_config["output"]) == 1


class TestAppConfigPostInit:
    def test_default_saved_prompts(self):
        config = AppConfig(saved_prompts=None)
        assert isinstance(config.saved_prompts, dict)

    def test_existing_dict_preserved(self):
        prompt_dict = {"content": "hello", "description": "desc", "output_dir": "", "include_transcript": False}
        config = AppConfig(saved_prompts={"p": prompt_dict})
        assert config.saved_prompts["p"]["content"] == "hello"

    def test_wizard_completed_default_false(self):
        """wizard_completedのデフォルト値はFalse"""
        config = AppConfig()
        assert config.wizard_completed is False

    def test_wizard_completed_can_set_true(self):
        """wizard_completedをTrueに設定できる"""
        config = AppConfig(wizard_completed=True)
        assert config.wizard_completed is True


class TestLoadConfig:
    def test_load_default_when_no_file(self, isolate_config):
        config = load_config()
        assert isinstance(config, AppConfig)
        assert config.whisper_model == "turbo"

    def test_load_from_json(self, write_config, sample_config_data):
        write_config()
        config = load_config()
        assert config.whisper_model == "turbo"

    def test_whisper_backend_is_always_vibe(self, write_config):
        """文字起こしエンジンはUIから選べず、設定ファイルの値にかかわらず常に"vibe"になる"""
        write_config({"whisper_backend": "python"})
        config = load_config()
        assert config.whisper_backend == "vibe"


class TestSaveConfig:
    def test_save_creates_file(self, isolate_config):
        config = AppConfig()
        save_config(config)
        assert isolate_config["config_file"].exists()

    def test_save_writes_json(self, isolate_config):
        config = AppConfig(whisper_model="base")
        save_config(config)
        with open(isolate_config["config_file"], encoding="utf-8") as f:
            data = json.load(f)
        assert data["whisper_model"] == "base"

    def test_save_preserves_all_fields(self, isolate_config):
        config = AppConfig(whisper_model="small", vibe_model="base")
        save_config(config)
        loaded = load_config()
        assert loaded.whisper_model == "small"
        assert loaded.vibe_model == "base"

    def test_save_wizard_completed(self, isolate_config):
        """wizard_completed=Trueを保存し、読み込めること"""
        config = AppConfig(wizard_completed=True)
        save_config(config)
        loaded = load_config()
        assert loaded.wizard_completed is True

    def test_save_wizard_completed_false(self, isolate_config):
        """wizard_completed=Falseを保存し、読み込めること"""
        config = AppConfig(wizard_completed=False)
        save_config(config)
        loaded = load_config()
        assert loaded.wizard_completed is False


class TestConfigMemoryCache:
    """load_config()が2回目以降はディスクを再読み込みせずメモリキャッシュを返すこと"""

    def test_second_call_does_not_reread_disk(self, write_config):
        write_config({"whisper_model": "base"})
        load_config()  # 1回目: ディスクから読み込みキャッシュに格納

        from src_backends import config as cfg
        with patch.object(cfg, "_load_config_from_disk") as mock_load_from_disk:
            second = load_config()
        mock_load_from_disk.assert_not_called()
        assert second.whisper_model == "base"

    def test_mutating_returned_config_does_not_leak_into_cache(self, write_config):
        write_config({"whisper_model": "base"})
        first = load_config()
        first.whisper_model = "mutated-locally"

        second = load_config()
        assert second.whisper_model == "base"

    def test_save_config_updates_cache_without_disk_reread(self, isolate_config):
        config = AppConfig(whisper_model="small")
        save_config(config)

        from src_backends import config as cfg
        with patch.object(cfg, "_load_config_from_disk") as mock_load_from_disk:
            loaded = load_config()
        mock_load_from_disk.assert_not_called()
        assert loaded.whisper_model == "small"

    def test_failed_save_does_not_update_cache(self, write_config, monkeypatch):
        write_config({"whisper_model": "base"})
        load_config()  # キャッシュに"base"を格納

        from src_backends import config as cfg
        monkeypatch.setattr(cfg.json, "dump", MagicMock(side_effect=RuntimeError("boom")))
        bad_config = AppConfig(whisper_model="should-not-be-cached")
        with pytest.raises(RuntimeError):
            save_config(bad_config)

        assert load_config().whisper_model == "base"

    def test_write_config_fixture_invalidates_cache(self, write_config):
        """write_configはsave_config()を経由せず直接ファイルを書き換えるため、
        テスト内で先にload_config()していても次のload_config()は新しい内容を返すこと"""
        write_config({"whisper_model": "base"})
        load_config()  # キャッシュに"base"を格納

        write_config({"whisper_model": "medium"})
        assert load_config().whisper_model == "medium"


class TestUpdateConfig:
    def test_update_single_field(self, write_config):
        write_config()
        updated = update_config(whisper_model="small")
        assert updated.whisper_model == "small"

    def test_update_multiple_fields(self, write_config):
        write_config()
        updated = update_config(whisper_model="base", dark_mode=True)
        assert updated.whisper_model == "base"
        assert updated.dark_mode is True


class TestIsVideoFile:
    def test_mp4(self):
        from src_backends.whisper_processor import is_video_file
        assert is_video_file(Path("video.mp4")) is True

    def test_wav(self):
        from src_backends.whisper_processor import is_video_file
        assert is_video_file(Path("audio.wav")) is False

    def test_mov(self):
        from src_backends.whisper_processor import is_video_file
        assert is_video_file(Path("clip.mov")) is True

    def test_txt(self):
        from src_backends.whisper_processor import is_video_file
        assert is_video_file(Path("doc.txt")) is False


class TestDefaultPromptConsolidation:
    """既定のプロンプト（文章校正）が prompt_sets の1箇所で定義されていることのテスト"""

    def test_appconfig_default_prompt_matches_constant(self):
        from src_backends.config import DEFAULT_PROMPT
        assert AppConfig().prompt == DEFAULT_PROMPT
        assert AppConfig().prompt == AppConfig().saved_prompts["文章校正"]["content"]

    def test_proofreading_prompt_keeps_everything(self):
        """文章校正は要約せず、元の内容をすべて残す指示を含む"""
        from src_backends.config import DEFAULT_PROMPT
        assert "要約は絶対にせず" in DEFAULT_PROMPT

    def test_local_llm_uses_same_constant(self):
        import src_backends.local_llm as llm
        from src_backends.config import DEFAULT_PROMPT
        assert llm.default_prompt("ja") == DEFAULT_PROMPT


class TestDefaultPrompts:
    """デフォルトプロンプトのテスト"""

    def test_default_saved_prompts_has_four_entries(self):
        """デフォルトのsaved_promptsが4つ（文章校正、企画書、報告書、お客様面談記録）の順で含まれること"""
        config = AppConfig(saved_prompts=None)
        assert list(config.saved_prompts) == ["文章校正", "企画書", "報告書", "お客様面談記録"]
        assert config.active_prompt_name == "文章校正"

    def test_proofreading_prompt_name_is_correct(self):
        """文章校正プロンプト名が「文章校正」であること"""
        config = AppConfig(saved_prompts=None)
        assert "文書校正" not in config.saved_prompts
        assert "文章校正" in config.saved_prompts

    def test_kikakusho_prompt_exists(self):
        """企画書プロンプトが存在すること"""
        config = AppConfig(saved_prompts=None)
        prompt = config.saved_prompts["企画書"]
        assert isinstance(prompt, dict)
        assert "content" in prompt
        assert "description" in prompt
        assert "背景・課題設定" in prompt["content"]


class TestResetConfig:
    """設定リセット機能のテスト"""

    def test_reset_prompt_tab(self, write_config):
        """プロンプトタブのリセット"""
        write_config()
        # プロンプトを変更
        config = load_config()
        config.saved_prompts = {"custom": {"content": "test", "description": "", "output_dir": "", "include_transcript": False}}
        config.active_prompt_name = "custom"
        save_config(config)

        # リセット実行
        reset_config("prompt")
        config = load_config()
        assert list(config.saved_prompts) == ["文章校正", "企画書", "報告書", "お客様面談記録"]
        assert config.active_prompt_name == "文章校正"
        assert config.prompt == config.saved_prompts["文章校正"]["content"]

    def test_reset_model_tab(self, write_config):
        """モデルタブのリセット"""
        write_config()
        # モデル設定を変更
        config = load_config()
        config.whisper_model = "tiny"
        config.vibe_model = "custom-model"
        save_config(config)

        # リセット実行
        reset_config("model")
        config = load_config()
        assert config.whisper_model == "turbo"
        assert config.vibe_model == "large-v3-turbo"

    def test_reset_all_preserves_wizard_completed(self, write_config):
        """全リセットでwizard_completedが保持されること"""
        write_config({"wizard_completed": True})
        reset_config("all")
        config = load_config()
        assert config.wizard_completed is True


class TestSaveLoadDefaultConfig:
    """デフォルト設定の保存/読み込みテスト"""

    def test_save_default_config_creates_file(self, isolate_config):
        """save_default_configでdefault.jsonが作成されること"""
        save_default_config()
        assert (isolate_config["base"] / "default.json").exists()

    def test_save_default_config_writes_json(self, isolate_config):
        """save_default_configでJSONが正しく書き込まれること"""
        save_default_config()
        with open(isolate_config["base"] / "default.json", encoding="utf-8") as f:
            data = json.load(f)
        assert "whisper_model" in data
        assert "saved_prompts" in data

    def test_load_default_config_returns_appconfig(self, isolate_config):
        """load_default_configがAppConfigを返すこと"""
        save_default_config()
        config = load_default_config()
        assert isinstance(config, AppConfig)

    def test_load_default_config_when_no_file(self, isolate_config):
        """default.jsonがない場合でもデフォルト値でAppConfigが返されること"""
        config = load_default_config()
        assert isinstance(config, AppConfig)
        assert config.whisper_model == "turbo"


class TestCheckVcRuntime:
    """check_vc_runtimeがSystem32のDLL存在確認で判定すること（GetModuleHandleWの戻り値は無視）"""

    def test_all_dlls_present_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
        system32 = tmp_path / "System32"
        system32.mkdir()
        for dll in VC_RUNTIME_DLLS:
            (system32 / dll).write_text("")
        assert check_vc_runtime() is True

    def test_missing_dll_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
        system32 = tmp_path / "System32"
        system32.mkdir()
        # 最初の1つ以外は用意し、1つだけ欠落させる
        for dll in VC_RUNTIME_DLLS[1:]:
            (system32 / dll).write_text("")
        assert check_vc_runtime() is False

    def test_no_dlls_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
        (tmp_path / "System32").mkdir()
        assert check_vc_runtime() is False

    def test_empty_systemroot_falls_back_to_c_windows(self, tmp_path, monkeypatch):
        """SYSTEMROOTが空文字列の場合でもos.environ.getのデフォルト値ではなくC:\\Windowsにフォールバックすること"""
        monkeypatch.setenv("SYSTEMROOT", "")
        monkeypatch.setattr(setup_checker, "get_app_dir", lambda: tmp_path)

        probed_paths = []
        real_exists = Path.exists

        def tracking_exists(self):
            probed_paths.append(str(self))
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", tracking_exists)
        check_vc_runtime()
        # SYSTEMROOT="" の os.environ.get デフォルト値は使われず、
        # 相対パス "System32\\..." ではなく "C:\\Windows\\System32\\..." が探索されること
        assert any(p.lower().startswith("c:\\windows\\system32") for p in probed_paths), probed_paths

    def test_app_local_dll_counts_as_present(self, tmp_path, monkeypatch):
        """System32になくてもアプリ同梱ディレクトリにDLLがあればOK扱いになること"""
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path / "no_such_windows"))
        monkeypatch.setattr(setup_checker, "get_app_dir", lambda: tmp_path)
        for dll in VC_RUNTIME_DLLS:
            (tmp_path / dll).write_text("")
        assert check_vc_runtime() is True


class TestCheckGpu:
    """check_gpu: PowerShellサブプロセスの結果をディスクキャッシュして再利用すること"""

    def test_no_cache_runs_subprocess_and_writes_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / "gpu_info_cache.json"

        mock_result = MagicMock(returncode=0, stdout="NVIDIA GeForce RTX 4060")
        with patch.object(setup_checker.subprocess, "run", return_value=mock_result) as mock_run:
            info = setup_checker.check_gpu()

        assert info == {"has_amd_igpu": False, "has_nvidia": True, "has_intel": False}
        mock_run.assert_called_once()
        assert cache_file.exists()
        assert json.loads(cache_file.read_text(encoding="utf-8")) == info

    def test_existing_cache_skips_subprocess(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / "gpu_info_cache.json"
        cached_info = {"has_amd_igpu": True, "has_nvidia": False, "has_intel": False}
        cache_file.write_text(json.dumps(cached_info), encoding="utf-8")

        with patch.object(setup_checker.subprocess, "run") as mock_run:
            info = setup_checker.check_gpu()

        assert info == cached_info
        mock_run.assert_not_called()

    def test_use_cache_false_forces_resubprocess(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / "gpu_info_cache.json"
        cache_file.write_text(json.dumps({"has_amd_igpu": True, "has_nvidia": False, "has_intel": False}), encoding="utf-8")

        mock_result = MagicMock(returncode=0, stdout="Intel(R) UHD Graphics")
        with patch.object(setup_checker.subprocess, "run", return_value=mock_result) as mock_run:
            info = setup_checker.check_gpu(use_cache=False)

        mock_run.assert_called_once()
        assert info == {"has_amd_igpu": False, "has_nvidia": False, "has_intel": True}

    def test_corrupt_cache_falls_back_to_subprocess(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / "gpu_info_cache.json"
        cache_file.write_text("not valid json", encoding="utf-8")

        mock_result = MagicMock(returncode=0, stdout="")
        with patch.object(setup_checker.subprocess, "run", return_value=mock_result) as mock_run:
            info = setup_checker.check_gpu()

        mock_run.assert_called_once()
        assert info == {"has_amd_igpu": False, "has_nvidia": False, "has_intel": False}

    def test_malformed_cache_shape_falls_back_to_subprocess(self, tmp_path, monkeypatch):
        """キャッシュファイルの中身が期待した形（dict + 3キー）でない場合は再検出すること"""
        monkeypatch.setattr(setup_checker, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / "gpu_info_cache.json"
        cache_file.write_text(json.dumps({"has_amd_igpu": True}), encoding="utf-8")  # キーが不足

        mock_result = MagicMock(returncode=0, stdout="")
        with patch.object(setup_checker.subprocess, "run", return_value=mock_result) as mock_run:
            info = setup_checker.check_gpu()

        mock_run.assert_called_once()
        assert info == {"has_amd_igpu": False, "has_nvidia": False, "has_intel": False}

    def test_failed_detection_is_not_cached(self, tmp_path, monkeypatch):
        """returncode!=0や出力なしの検出失敗時は、次回また再検出できるようキャッシュに書き込まないこと"""
        monkeypatch.setattr(setup_checker, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / "gpu_info_cache.json"

        mock_result = MagicMock(returncode=1, stdout="")
        with patch.object(setup_checker.subprocess, "run", return_value=mock_result):
            info = setup_checker.check_gpu()

        assert info == {"has_amd_igpu": False, "has_nvidia": False, "has_intel": False}
        assert not cache_file.exists()

    def test_empty_output_with_zero_returncode_is_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "CACHE_DIR", tmp_path)
        cache_file = tmp_path / "gpu_info_cache.json"

        mock_result = MagicMock(returncode=0, stdout="   ")
        with patch.object(setup_checker.subprocess, "run", return_value=mock_result):
            setup_checker.check_gpu()

        assert not cache_file.exists()


class TestDownloadAndVerify:
    """_download_and_verify: ダウンロード + SHA256検証 + 一意な一時ディレクトリのテスト"""

    def _fake_content(self):
        content = b"fake-installer-content"
        import hashlib
        return content, hashlib.sha256(content).hexdigest().upper()

    def _patch_urlopen(self, monkeypatch, content):
        import io

        def fake_urlopen(url, timeout=None):
            return io.BytesIO(content)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    def test_hash_match_returns_path(self, monkeypatch):
        content, expected_hash = self._fake_content()
        self._patch_urlopen(monkeypatch, content)
        result = _download_and_verify("http://example.com/x.exe", "x.exe", expected_hash)
        try:
            assert result is not None
            assert result.exists()
            assert result.read_bytes() == content
        finally:
            if result is not None:
                shutil_rmtree_safe(result.parent)

    def test_hash_mismatch_returns_none_and_cleans_up(self, tmp_path, monkeypatch):
        content, _ = self._fake_content()
        self._patch_urlopen(monkeypatch, content)

        fixed_temp_dir = tmp_path / "voice2doc_fixed"
        monkeypatch.setattr(setup_checker.tempfile, "mkdtemp", lambda prefix=None: str(fixed_temp_dir))
        fixed_temp_dir.mkdir()

        result = _download_and_verify("http://example.com/x.exe", "x.exe", "0" * 64)
        assert result is None
        assert not fixed_temp_dir.exists()

    def test_download_error_returns_none_and_cleans_up(self, tmp_path, monkeypatch):
        def fake_urlopen(url, timeout=None):
            raise OSError("network error")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        fixed_temp_dir = tmp_path / "voice2doc_fixed"
        monkeypatch.setattr(setup_checker.tempfile, "mkdtemp", lambda prefix=None: str(fixed_temp_dir))
        fixed_temp_dir.mkdir()

        result = _download_and_verify("http://example.com/x.exe", "x.exe", "0" * 64)
        assert result is None
        assert not fixed_temp_dir.exists()

    def test_each_call_uses_unique_directory(self, monkeypatch):
        content, expected_hash = self._fake_content()
        self._patch_urlopen(monkeypatch, content)
        result1 = _download_and_verify("http://example.com/x.exe", "x.exe", expected_hash)
        result2 = _download_and_verify("http://example.com/x.exe", "x.exe", expected_hash)
        try:
            assert result1.parent != result2.parent
        finally:
            shutil_rmtree_safe(result1.parent)
            shutil_rmtree_safe(result2.parent)


def shutil_rmtree_safe(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


class TestInstallVcRuntimeAndWebview2:
    """install_vc_runtime / install_webview2 のハッシュ検証統合テスト"""

    def test_install_vc_runtime_skips_when_already_present(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: True)
        called = {"download": False}
        monkeypatch.setattr(
            setup_checker, "_download_and_verify",
            lambda *a, **k: called.__setitem__("download", True),
        )
        assert install_vc_runtime() is True
        assert called["download"] is False

    def test_install_vc_runtime_fails_when_hash_verification_fails(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: False)
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: None)
        called = {"subprocess": False}

        def fake_run(*a, **k):
            called["subprocess"] = True
            return MagicMock(returncode=0)

        monkeypatch.setattr(setup_checker.subprocess, "run", fake_run)
        assert install_vc_runtime() is False
        assert called["subprocess"] is False

    def test_install_vc_runtime_succeeds_and_cleans_up_temp_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: False)
        fake_installer = tmp_path / "install_dir" / "vc_redist.x64.exe"
        fake_installer.parent.mkdir()
        fake_installer.write_bytes(b"x")
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: fake_installer)
        monkeypatch.setattr(setup_checker.subprocess, "run", lambda *a, **k: MagicMock(returncode=0))

        assert install_vc_runtime() is True
        assert not fake_installer.parent.exists()

    def test_install_vc_runtime_cleans_up_temp_dir_even_if_subprocess_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: False)
        fake_installer = tmp_path / "install_dir" / "vc_redist.x64.exe"
        fake_installer.parent.mkdir()
        fake_installer.write_bytes(b"x")
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: fake_installer)

        def raising_run(*a, **k):
            raise OSError("実行に失敗しました（テスト用）")

        monkeypatch.setattr(setup_checker.subprocess, "run", raising_run)

        assert install_vc_runtime() is False
        assert not fake_installer.parent.exists()

    def test_install_webview2_fails_when_hash_verification_fails(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: False)
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: None)
        assert install_webview2() is False

    def test_install_webview2_skips_when_already_present(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: True)
        called = {"download": False}
        monkeypatch.setattr(
            setup_checker, "_download_and_verify",
            lambda *a, **k: called.__setitem__("download", True),
        )
        assert install_webview2() is True
        assert called["download"] is False

    def test_install_webview2_succeeds_on_returncode_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: False)
        fake_installer = tmp_path / "install_dir" / "MicrosoftEdgeWebview2Setup.exe"
        fake_installer.parent.mkdir()
        fake_installer.write_bytes(b"x")
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: fake_installer)
        monkeypatch.setattr(
            setup_checker.subprocess, "run",
            lambda *a, **k: MagicMock(returncode=0, stdout=b"ok", stderr=b""),
        )
        assert install_webview2() is True
        assert not fake_installer.parent.exists()

    def test_install_webview2_already_installed_error_code_treated_as_success(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def fake_check():
            calls["n"] += 1
            return calls["n"] > 1  # インストール前チェックはFalse、インストール後はTrue

        monkeypatch.setattr(setup_checker, "check_webview2", fake_check)
        fake_installer = tmp_path / "install_dir" / "MicrosoftEdgeWebview2Setup.exe"
        fake_installer.parent.mkdir()
        fake_installer.write_bytes(b"x")
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: fake_installer)
        monkeypatch.setattr(
            setup_checker.subprocess, "run",
            lambda *a, **k: MagicMock(returncode=2147747880, stdout=b"", stderr=b""),
        )
        assert install_webview2() is True

    def test_install_webview2_other_error_code_but_check_passes(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def fake_check():
            calls["n"] += 1
            return calls["n"] > 1

        monkeypatch.setattr(setup_checker, "check_webview2", fake_check)
        fake_installer = tmp_path / "install_dir" / "MicrosoftEdgeWebview2Setup.exe"
        fake_installer.parent.mkdir()
        fake_installer.write_bytes(b"x")
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: fake_installer)
        monkeypatch.setattr(
            setup_checker.subprocess, "run",
            lambda *a, **k: MagicMock(returncode=1603, stdout=b"", stderr=b""),
        )
        assert install_webview2() is True

    def test_install_webview2_fails_when_check_still_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: False)
        fake_installer = tmp_path / "install_dir" / "MicrosoftEdgeWebview2Setup.exe"
        fake_installer.parent.mkdir()
        fake_installer.write_bytes(b"x")
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: fake_installer)
        monkeypatch.setattr(
            setup_checker.subprocess, "run",
            lambda *a, **k: MagicMock(returncode=1603, stdout=b"", stderr=b""),
        )
        assert install_webview2() is False

    def test_install_webview2_cleans_up_temp_dir_even_if_subprocess_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: False)
        fake_installer = tmp_path / "install_dir" / "MicrosoftEdgeWebview2Setup.exe"
        fake_installer.parent.mkdir()
        fake_installer.write_bytes(b"x")
        monkeypatch.setattr(setup_checker, "_download_and_verify", lambda *a, **k: fake_installer)
        monkeypatch.setattr(
            setup_checker.subprocess, "run",
            MagicMock(side_effect=OSError("実行に失敗しました（テスト用）")),
        )
        assert install_webview2() is False
        assert not fake_installer.parent.exists()


class TestCheckWebview2:
    """check_webview2: レジストリ(HKLM→HKCU)→ファイルシステムの順にフォールバックすること"""

    def test_hklm_registry_found(self, monkeypatch):
        import winreg

        fake_key = MagicMock()
        fake_key.__enter__.return_value = fake_key
        fake_key.__exit__.return_value = False
        with patch.object(winreg, "OpenKey", return_value=fake_key), \
             patch.object(winreg, "QueryValueEx", return_value=("120.0.1", 1)):
            assert setup_checker.check_webview2() is True

    def test_hkcu_registry_found_when_hklm_missing(self, monkeypatch):
        import winreg

        fake_key = MagicMock()
        fake_key.__enter__.return_value = fake_key
        fake_key.__exit__.return_value = False

        def fake_open_key(hive, path):
            if hive == winreg.HKEY_LOCAL_MACHINE:
                raise FileNotFoundError()
            return fake_key

        with patch.object(winreg, "OpenKey", side_effect=fake_open_key), \
             patch.object(winreg, "QueryValueEx", return_value=("120.0.1", 1)):
            assert setup_checker.check_webview2() is True

    def test_filesystem_fallback_found(self, tmp_path, monkeypatch):
        import winreg

        edge_dir = tmp_path / "Microsoft" / "Edge" / "Application"
        edge_dir.mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "no_such_pf"))
        monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "no_such_pf86"))

        with patch.object(winreg, "OpenKey", side_effect=FileNotFoundError()):
            assert setup_checker.check_webview2() is True

    def test_not_found_anywhere_returns_false(self, tmp_path, monkeypatch):
        import winreg

        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no_such_local"))
        monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "no_such_pf"))
        monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "no_such_pf86"))

        with patch.object(winreg, "OpenKey", side_effect=FileNotFoundError()):
            assert setup_checker.check_webview2() is False


class TestCheckFfmpeg:
    """ffmpeg.exe は同梱せず、Vibe のフォルダ（vibe-server.exe の隣）か PATH のものを使う"""

    def test_ffmpeg_in_vibe_folder_found(self, tmp_path, monkeypatch):
        from src_backends import vibe_client
        (tmp_path / "vibe-server.exe").write_text("")
        (tmp_path / "ffmpeg.exe").write_text("")
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": tmp_path / "vibe-server.exe")
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: None)
        assert setup_checker.check_ffmpeg() is True

    def test_configured_vibe_path_is_used(self, tmp_path, monkeypatch, write_config):
        from src_backends import vibe_client
        vibe = tmp_path / "MyVibe"
        vibe.mkdir()
        (vibe / "vibe-server.exe").write_text("")
        (vibe / "ffmpeg.exe").write_text("")
        write_config({"vibe_server_path": str(vibe / "vibe-server.exe")})
        monkeypatch.setattr(vibe_client, "VIBE_BINARY_CANDIDATES", [])
        monkeypatch.setattr(vibe_client, "_find_vibe_install_dir_from_registry", lambda: None)
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: None)
        assert setup_checker.check_ffmpeg() is True

    def test_found_via_path_when_vibe_has_none(self, monkeypatch):
        from src_backends import vibe_client
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": None)
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: "C:/tools/ffmpeg.exe")
        assert setup_checker.check_ffmpeg() is True

    def test_not_found_returns_false(self, monkeypatch):
        from src_backends import vibe_client
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": None)
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: None)
        assert setup_checker.check_ffmpeg() is False

    def test_bundled_app_folder_is_no_longer_searched(self, tmp_path, monkeypatch):
        from src_backends import vibe_client
        (tmp_path / "vibe").mkdir()
        (tmp_path / "vibe" / "ffmpeg.exe").write_text("")
        (tmp_path / "ffmpeg.exe").write_text("")
        monkeypatch.setattr(setup_checker, "get_app_dir", lambda: tmp_path)
        monkeypatch.setattr(vibe_client, "find_vibe_binary", lambda custom_path="": None)
        monkeypatch.setattr(vibe_client.shutil, "which", lambda name: None)
        assert setup_checker.check_ffmpeg() is False


class TestCheckVibe:
    """check_vibe()がvibe_client.find_vibe_binary()に委譲していることのテスト"""

    def test_delegates_to_vibe_client_find_vibe_binary(self, tmp_path, monkeypatch):
        from src_backends import vibe_client
        found = tmp_path / "vibe-server.exe"
        found.write_text("")
        monkeypatch.setattr(vibe_client, "detect_vibe_binary", lambda custom_path="", deep=False: (found, []))
        assert setup_checker.check_vibe() is True

    def test_returns_false_when_not_found(self, monkeypatch):
        from src_backends import vibe_client
        monkeypatch.setattr(vibe_client, "detect_vibe_binary", lambda custom_path="", deep=False: (None, []))
        assert setup_checker.check_vibe() is False


class TestRunAllChecks:
    def test_aggregates_all_check_results(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: True)
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: False)
        monkeypatch.setattr(setup_checker, "check_ffmpeg", lambda: True)
        monkeypatch.setattr(setup_checker, "check_vibe", lambda: True)
        monkeypatch.setattr(setup_checker, "check_disk_space", lambda required_gb=10.0: True)
        monkeypatch.setattr(setup_checker, "check_gpu", lambda: {"has_amd_igpu": False, "has_nvidia": True, "has_intel": False})

        results = setup_checker.run_all_checks()
        assert results == {
            "vc_runtime": True,
            "webview2": False,
            "ffmpeg": True,
            "vibe": True,
            "disk_space": True,
            "gpu": {"has_amd_igpu": False, "has_nvidia": True, "has_intel": False},
        }

    def test_progress_callback_invoked(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: True)
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: True)
        monkeypatch.setattr(setup_checker, "check_ffmpeg", lambda: True)
        monkeypatch.setattr(setup_checker, "check_vibe", lambda: True)
        monkeypatch.setattr(setup_checker, "check_disk_space", lambda required_gb=10.0: True)
        monkeypatch.setattr(setup_checker, "check_gpu", lambda: {})

        messages = []
        setup_checker.run_all_checks(progress_callback=lambda msg: messages.append(msg))
        assert messages


class TestEnsureAllPrerequisites:
    def test_all_ok_returns_true_without_installing(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: True)
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: True)
        monkeypatch.setattr(
            setup_checker, "run_all_checks",
            lambda progress_callback=None: {
                "vc_runtime": True, "webview2": True, "ffmpeg": True,
                "vibe": True, "disk_space": True, "gpu": {},
            },
        )
        install_vc = MagicMock()
        install_wv = MagicMock()
        monkeypatch.setattr(setup_checker, "install_vc_runtime", install_vc)
        monkeypatch.setattr(setup_checker, "install_webview2", install_wv)

        assert setup_checker.ensure_all_prerequisites() is True
        install_vc.assert_not_called()
        install_wv.assert_not_called()

    def test_missing_prereqs_trigger_install_and_result_reflects_final_check(self, monkeypatch):
        monkeypatch.setattr(setup_checker, "check_vc_runtime", lambda: False)
        monkeypatch.setattr(setup_checker, "check_webview2", lambda: False)
        install_vc = MagicMock(return_value=True)
        install_wv = MagicMock(return_value=False)
        monkeypatch.setattr(setup_checker, "install_vc_runtime", install_vc)
        monkeypatch.setattr(setup_checker, "install_webview2", install_wv)
        monkeypatch.setattr(
            setup_checker, "run_all_checks",
            lambda progress_callback=None: {
                "vc_runtime": True, "webview2": False, "ffmpeg": True,
                "vibe": True, "disk_space": True, "gpu": {},
            },
        )

        assert setup_checker.ensure_all_prerequisites() is False
        install_vc.assert_called_once()
        install_wv.assert_called_once()

