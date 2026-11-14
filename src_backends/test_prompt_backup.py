"""保存済みプロンプトのバックアップ/復元と「このプロンプトを使わない」のテスト"""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from src_backends import i18n, prompt_backup, prompt_sets
from src_backends.config import APP_VERSION, AppConfig

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """ダウンロード先(~/Downloads)を実環境から隔離する"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _backup(prompts: dict, **extra) -> str:
    return json.dumps({"app": "Voice2Doc", "kind": "prompts", "format_version": 1, "prompts": prompts, **extra},
                      ensure_ascii=False)


def _prompt(content="本文", **kw):
    return {"content": content, "description": "", "output_dir": "", "include_transcript": False, **kw}


class TestBuildAndParse:
    def test_backup_round_trips_every_field(self):
        full = _prompt("A", description="説明", output_dir="C:\\out", include_transcript=True, hidden=True)
        saved = {"報告書": full, "旧形式": "本文だけ"}
        raw = prompt_backup.build_backup(saved, "9.9.9", now=datetime(2026, 11, 3, 9, 8, 7))
        data = json.loads(raw.decode("utf-8"))
        assert data["app"] == "Voice2Doc" and data["kind"] == "prompts" and data["format_version"] == 1
        assert data["app_version"] == "9.9.9" and data["exported_at"].startswith("2026-11-03T09:08:07")
        parsed = prompt_backup.parse_backup(raw)
        assert parsed["報告書"] == full
        assert parsed["旧形式"] == _prompt("本文だけ", hidden=False)

    def test_filename(self):
        assert prompt_backup.backup_filename(datetime(2026, 1, 2, 3, 4, 5)) == "Voice2Doc-prompts-20260102-030405"

    def test_missing_fields_default_and_unknown_keys_are_ignored(self):
        parsed = prompt_backup.parse_backup(_backup({"x": {"content": "c", "unknown": 1}}))
        assert parsed == {"x": _prompt("c", hidden=False)}

    def test_bom_is_accepted(self):
        assert "x" in prompt_backup.parse_backup(b"\xef\xbb\xbf" + _backup({"x": _prompt()}).encode("utf-8"))

    @pytest.mark.parametrize("raw,key", [
        ("not json", "backup.invalid_json"),
        (b"\xff\xfe\x00", "backup.invalid_json"),
        ("[]", "backup.not_a_backup"),
        (json.dumps({"kind": "history", "prompts": {}}), "backup.not_a_backup"),
        (json.dumps({"kind": "prompts", "prompts": []}), "backup.not_a_backup"),
        (_backup({}), "backup.empty"),
        (_backup({" ": _prompt()}), "backup.invalid_name"),
        (_backup({"x" * 201: _prompt()}), "backup.invalid_name"),
        (_backup({"x": {"content": 5}}), "backup.invalid_prompt"),
        (_backup({"x": {"content": "c", "output_dir": None}}), "backup.invalid_prompt"),
        (_backup({"x": 3}), "backup.invalid_prompt"),
        (_backup({"x": {"content": "c", "hidden": "false"}}), "backup.invalid_prompt"),
        (_backup({"x": {"content": "c", "include_transcript": 1}}), "backup.invalid_prompt"),
    ])
    def test_invalid_files_are_rejected(self, raw, key):
        with pytest.raises(prompt_backup.PromptBackupError) as e:
            prompt_backup.parse_backup(raw)
        assert e.value.key == key

    def test_too_large(self):
        with pytest.raises(prompt_backup.PromptBackupError) as e:
            prompt_backup.parse_backup(b" " * (prompt_backup.MAX_BACKUP_BYTES + 1))
        assert e.value.key == "backup.too_large"

    def test_error_messages_exist_in_both_languages(self):
        source = Path(prompt_backup.__file__).read_text(encoding="utf-8")
        keys = set(re.findall(r'PromptBackupError\("([a-z_.]+)"', source))
        assert keys and keys <= set(i18n.MESSAGES["ja"]) and keys <= set(i18n.MESSAGES["en"])


class TestApplyRestore:
    def test_merge_adds_overwrites_and_keeps_others(self):
        config = AppConfig()
        config.saved_prompts["自作"] = _prompt("残る")
        prompt_backup.apply_restore(config, {"文章校正": _prompt("新しい校正"), "追加": _prompt("追加")}, "merge")
        assert config.saved_prompts["文章校正"]["content"] == "新しい校正"
        assert config.saved_prompts["追加"]["content"] == "追加"
        assert config.saved_prompts["自作"]["content"] == "残る"
        assert "報告書" in config.saved_prompts
        # 使用中のプロンプトの本文も更新
        assert config.active_prompt_name == "文章校正" and config.prompt == "新しい校正"

    def test_replace_removes_user_prompts_but_keeps_builtins(self):
        config = AppConfig()
        config.saved_prompts["自作"] = _prompt("消える")
        prompt_backup.apply_restore(config, {"復元": _prompt("復元")}, "replace")
        assert "自作" not in config.saved_prompts
        assert {"文章校正", "企画書", "報告書", "お客様面談記録", "復元"} == set(config.saved_prompts)

    def test_replace_uses_the_backup_version_of_a_builtin(self):
        config = AppConfig()
        prompt_backup.apply_restore(config, {"企画書": _prompt("編集版")}, "replace")
        assert config.saved_prompts["企画書"]["content"] == "編集版"

    def test_active_prompt_that_disappears_falls_back_to_default(self):
        config = AppConfig()
        config.saved_prompts["自作"] = _prompt("自作")
        config.active_prompt_name, config.prompt = "自作", "自作"
        prompt_backup.apply_restore(config, {"復元": _prompt("復元")}, "replace")
        assert config.active_prompt_name == prompt_sets.default_active_prompt_name("ja")
        assert config.prompt == config.saved_prompts["文章校正"]["content"]

    def test_invalid_mode(self):
        with pytest.raises(prompt_backup.PromptBackupError):
            prompt_backup.apply_restore(AppConfig(), {"x": _prompt()}, "append")

    def test_preview(self):
        info = prompt_backup.preview({"報告書": _prompt(), "自作": _prompt()}, {"報告書": _prompt(), "新": _prompt()})
        assert info["count"] == 2 and info["names"] == ["報告書", "新"]
        assert info["conflicts"] == ["報告書"]
        assert info["builtin_kept"] == []
        assert prompt_backup.preview({"企画書": _prompt()}, {"新": _prompt()})["builtin_kept"] == ["企画書"]


class TestBackupRestoreApi:
    def test_backup_writes_to_downloads(self, test_client, fake_home):
        test_client.post("/prompts", json={"name": "自作", "content": "c", "hidden": True})
        resp = test_client.post("/prompts/backup").json()
        assert resp["status"] == "ok"
        path = Path(resp["path"])
        assert path.parent == fake_home / "Downloads"
        assert re.fullmatch(r"Voice2Doc-prompts-\d{8}-\d{6}(_\d+)?\.json", path.name)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["app_version"] == APP_VERSION
        assert data["prompts"]["自作"]["hidden"] is True
        assert {"文章校正", "企画書", "報告書", "お客様面談記録", "自作"} <= set(data["prompts"])

    def test_two_backups_in_the_same_second_do_not_overwrite(self, test_client, fake_home, monkeypatch):
        monkeypatch.setattr(prompt_backup, "backup_filename", lambda now=None: "Voice2Doc-prompts-same")
        first = test_client.post("/prompts/backup").json()["path"]
        second = test_client.post("/prompts/backup").json()["path"]
        assert first != second and Path(first).exists() and Path(second).exists()

    def test_backup_then_restore_from_path(self, test_client, fake_home):
        test_client.post("/prompts", json={"name": "自作", "content": "元の内容"})
        path = test_client.post("/prompts/backup").json()["path"]
        test_client.post("/prompts", json={"name": "自作", "content": "変更後"})
        test_client.post("/prompts", json={"name": "後から", "content": "x"})

        info = test_client.post("/prompts/restore/preview", json={"path": path}).json()
        assert info["status"] == "ok" and "自作" in info["conflicts"]

        resp = test_client.post("/prompts/restore", json={"path": path, "mode": "merge"}).json()
        assert resp["status"] == "ok" and resp["count"] == 5  # 初期プロンプト4つ + 自作
        prompts = test_client.get("/prompts").json()["prompts"]
        assert prompts["自作"]["content"] == "元の内容" and "後から" in prompts

        test_client.post("/prompts/restore", json={"path": path, "mode": "replace"})
        assert "後から" not in test_client.get("/prompts").json()["prompts"]

    def test_restore_from_content(self, test_client):
        resp = test_client.post("/prompts/restore", json={"content": _backup({"貼付": _prompt("p")}), "mode": "merge"})
        assert resp.json()["status"] == "ok"
        assert test_client.get("/prompts").json()["prompts"]["貼付"]["content"] == "p"

    def test_invalid_backup_returns_an_error_and_changes_nothing(self, test_client):
        before = test_client.get("/prompts").json()["prompts"]
        resp = test_client.post("/prompts/restore", json={"content": "{}", "mode": "replace"}).json()
        assert resp["status"] == "error" and "バックアップファイルではありません" in resp["message"]
        assert test_client.get("/prompts").json()["prompts"] == before

    def test_missing_file(self, test_client, tmp_path):
        resp = test_client.post("/prompts/restore/preview", json={"path": str(tmp_path / "none.json")}).json()
        assert resp["status"] == "error" and "見つかりません" in resp["message"]
        assert test_client.post("/prompts/restore/preview", json={}).json()["status"] == "error"

    def test_too_large_file_is_not_read(self, test_client, tmp_path):
        big = tmp_path / "big.json"
        big.write_bytes(b" " * (prompt_backup.MAX_BACKUP_BYTES + 1))
        assert "大きすぎます" in test_client.post("/prompts/restore/preview", json={"path": str(big)}).json()["message"]

    def test_invalid_mode_is_rejected(self, test_client):
        content = _backup({"x": _prompt()})
        assert test_client.post("/prompts/restore", json={"content": content, "mode": "x"}).status_code == 400
        assert test_client.post("/prompts/restore", json={"content": content}).status_code == 400

    def test_messages_follow_the_language(self, test_client):
        test_client.post("/config", json={"ui_language": "en"})
        resp = test_client.post("/prompts/restore/preview", json={"content": "oops"}).json()
        assert resp["message"] == "The file is not in JSON format"

    def test_dialog_kind_for_backups(self, test_client, monkeypatch, fake_home):
        from src_backends import main
        seen = {}

        def fake_dialog(kind, initial):
            seen.update(kind=kind, initial=initial)
            return {"status": "cancel"}

        monkeypatch.setattr(main, "_run_pick_dialog", fake_dialog)
        assert test_client.post("/dialog/pick", json={"kind": "prompts_json"}).json()["status"] == "cancel"
        assert seen == {"kind": "prompts_json", "initial": str(fake_home / "Downloads")}
        i18n.set_language("en")
        assert main._dialog_file_types("prompts_json")[0] == "Prompt backups (*.json)"


class TestHiddenPrompt:
    def test_hidden_is_saved_and_kept_when_omitted(self, test_client):
        test_client.post("/prompts", json={"name": "隠す", "content": "c", "hidden": True})
        test_client.post("/prompts", json={"name": "隠す", "content": "c2"})
        data = test_client.get("/prompts").json()["prompts"]["隠す"]
        assert data["hidden"] is True and data["content"] == "c2"
        test_client.post("/prompts", json={"name": "隠す", "hidden": False})
        assert test_client.get("/prompts").json()["prompts"]["隠す"]["hidden"] is False

    def test_default_is_not_hidden(self, test_client):
        test_client.post("/prompts", json={"name": "新規", "content": "c"})
        prompts = test_client.get("/prompts").json()["prompts"]
        assert prompts["新規"]["hidden"] is False
        assert all(p["hidden"] is False for name, p in prompts.items() if name in prompt_sets.builtin_names())

    def test_legacy_string_prompt_becomes_not_hidden(self, test_client, isolate_config):
        from src_backends import config as cfg
        config = cfg.load_config()
        config.saved_prompts["旧"] = "本文だけ"
        cfg.save_config(config)
        test_client.post("/prompts", json={"name": "旧", "description": "d"})
        assert test_client.get("/prompts").json()["prompts"]["旧"]["hidden"] is False

    def test_language_switch_keeps_hidden(self):
        config = AppConfig()
        config.saved_prompts["企画書"]["hidden"] = True
        prompt_sets.switch_language(config, "en")
        assert config.saved_prompts["Proposal"]["hidden"] is True
        prompt_sets.switch_language(config, "ja")
        assert config.saved_prompts["企画書"]["hidden"] is True

    def test_reset_shows_all_prompts_again(self, test_client):
        test_client.post("/prompts", json={"name": "報告書", "hidden": True})
        test_client.post("/config/reset", json={"tab": "prompt"})
        assert test_client.get("/prompts").json()["prompts"]["報告書"]["hidden"] is False


class TestFrontend:
    HTML = (SRC / "index.html").read_text(encoding="utf-8")
    APP = (SRC / "app.js").read_text(encoding="utf-8")

    def test_checkbox_is_between_name_and_description_and_unchecked(self):
        html = self.HTML
        assert html.index('id="prompt-name"') < html.index('id="prompt-hidden"') < html.index('id="prompt-description"')
        tag = re.search(r'<input[^>]*id="prompt-hidden"[^>]*>', html).group(0)
        assert 'type="checkbox"' in tag and "checked" not in tag

    def test_backup_and_restore_buttons_are_in_the_saved_prompts_header(self):
        header = re.search(r'<div class="prompt-list-header">(.*?)<div class="prompt-list"', self.HTML, re.S).group(1)
        for element in ('id="btn-backup-prompts"', 'id="btn-restore-prompts"'):
            assert element in header
        for button in ("btn-restore-merge", "btn-restore-replace", "btn-restore-cancel"):
            assert f'id="{button}"' in self.HTML

    def test_process_menu_skips_hidden_prompts(self):
        body = self.APP[self.APP.index("function renderPromptCards()"):]
        body = body[:body.index("promptNames.forEach")]
        assert "isPromptHidden" in body and "app.no_visible_prompts" in body

    def test_hidden_flag_is_sent_when_saving(self):
        assert "include_transcript: includeTranscript, hidden }" in self.APP
        assert "hidden: false })" in self.APP  # コピーは「使う」状態で作る

    def test_file_input_is_outside_the_settings_tab(self):
        """ファイルを選んだだけで設定タブが「未保存」にならないよう、タブの外に置く"""
        tab = re.search(r'<div class="tab-content active" id="tab-prompt-settings">(.*?)<!-- モデル設定タブ -->',
                        self.HTML, re.S).group(1)
        assert 'id="restore-prompts-file"' not in tab and 'id="restore-prompts-file"' in self.HTML

    def test_restore_discards_unsaved_changes_of_every_tab_first(self):
        body = self.APP[self.APP.index("getElementById('btn-restore-prompts')"):]
        body = body[:body.index("/dialog/pick")]
        assert "hasDirty()" in body and "askDiscardSettings()" in body and "clearAllDirty()" in body

    def test_escape_closes_the_restore_dialog(self):
        keydown = self.APP[self.APP.index("if (e.key === 'Escape')"):]
        assert "btn-restore-cancel" in keydown[:keydown.index("processing-nav-confirm-modal")]
