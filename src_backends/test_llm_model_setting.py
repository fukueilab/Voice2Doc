"""AIモデルの指定 (llm_model_path) と show_console 廃止のテスト"""
import json
from pathlib import Path

import pytest

from src_backends import config as cfg
from src_backends import local_llm


@pytest.fixture(autouse=True)
def _reset_llm_client(monkeypatch):
    monkeypatch.setattr(local_llm, "_llamacpp_client", None)


class TestNormalizeLlmModelPath:
    def test_empty_means_default(self):
        assert cfg.normalize_llm_model_path("") == ""
        assert cfg.normalize_llm_model_path("   ") == ""
        assert cfg.normalize_llm_model_path(None) == ""

    def test_default_label_means_default(self):
        assert cfg.normalize_llm_model_path(cfg.DEFAULT_LLM_MODEL_LABEL) == ""

    def test_existing_gguf_is_kept(self, tmp_path):
        model = tmp_path / "my-model.gguf"
        model.write_text("x")
        assert cfg.normalize_llm_model_path(str(model)) == str(model.resolve())

    def test_surrounding_quotes_are_stripped(self, tmp_path):
        """エクスプローラーの「パスのコピー」は前後に引用符が付く"""
        model = tmp_path / "my-model.gguf"
        model.write_text("x")
        assert cfg.normalize_llm_model_path(f'"{model}"') == str(model.resolve())

    def test_uppercase_extension_is_accepted(self, tmp_path):
        model = tmp_path / "MODEL.GGUF"
        model.write_text("x")
        assert cfg.normalize_llm_model_path(str(model)) == str(model.resolve())

    def test_relative_path_is_saved_as_absolute(self, tmp_path, monkeypatch):
        model = tmp_path / "rel.gguf"
        model.write_text("x")
        monkeypatch.chdir(tmp_path)
        assert cfg.normalize_llm_model_path("rel.gguf") == str(model.resolve())

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="見つかりません"):
            cfg.normalize_llm_model_path(str(tmp_path / "nope.gguf"))

    def test_non_gguf_raises(self, tmp_path):
        model = tmp_path / "model.bin"
        model.write_text("x")
        with pytest.raises(ValueError, match=r"\.gguf"):
            cfg.normalize_llm_model_path(str(model))

    def test_directory_raises(self, tmp_path):
        folder = tmp_path / "dir.gguf"
        folder.mkdir()
        with pytest.raises(ValueError):
            cfg.normalize_llm_model_path(str(folder))


class TestAppConfigFields:
    def test_llm_model_path_defaults_to_empty(self):
        assert cfg.AppConfig().llm_model_path == ""

    def test_show_console_field_removed(self):
        assert not hasattr(cfg.AppConfig(), "show_console")

    def test_legacy_show_console_key_in_config_json_is_ignored(self, isolate_config):
        isolate_config["config_file"].write_text(
            json.dumps({"show_console": True, "whisper_model": "tiny"}), encoding="utf-8"
        )
        cfg.invalidate_config_cache()
        config = cfg.load_config()
        assert config.whisper_model == "tiny"
        assert not hasattr(config, "show_console")
        # 未知のキーは次の保存で消える
        cfg.save_config(config)
        saved = json.loads(isolate_config["config_file"].read_text(encoding="utf-8"))
        assert "show_console" not in saved

    def test_reset_model_tab_restores_llm_model_path(self, isolate_config):
        cfg.save_default_config()  # 既定値(llm_model_path="")をdefault.jsonへ
        config = cfg.load_config()
        config.llm_model_path = "C:/x/custom.gguf"
        cfg.save_config(config)
        result = cfg.reset_config("model")
        assert result.llm_model_path == ""


class TestConfigApi:
    def test_get_config_returns_llm_model_path_and_no_show_console(self, test_client, write_config):
        write_config({"llm_model_path": ""})
        data = test_client.get("/config").json()
        assert data["llm_model_path"] == ""
        assert data["llm_model_default_label"] == cfg.DEFAULT_LLM_MODEL_LABEL
        assert "show_console" not in data

    def test_post_valid_model_path_is_saved(self, test_client, write_config, tmp_path):
        write_config()
        model = tmp_path / "custom.gguf"
        model.write_text("x")
        resp = test_client.post("/config", json={"llm_model_path": str(model)})
        assert resp.json()["status"] == "ok"
        assert test_client.get("/config").json()["llm_model_path"] == str(model.resolve())

    def test_post_default_label_clears_model_path(self, test_client, write_config, tmp_path):
        model = tmp_path / "custom.gguf"
        model.write_text("x")
        write_config({"llm_model_path": str(model)})
        resp = test_client.post("/config", json={"llm_model_path": cfg.DEFAULT_LLM_MODEL_LABEL})
        assert resp.json()["status"] == "ok"
        assert test_client.get("/config").json()["llm_model_path"] == ""

    def test_post_missing_model_file_returns_error_and_keeps_old_value(self, test_client, write_config, tmp_path):
        write_config({"whisper_model": "tiny"})
        resp = test_client.post("/config", json={
            "llm_model_path": str(tmp_path / "nope.gguf"),
            "whisper_model": "small",
        })
        body = resp.json()
        assert body["status"] == "error"
        assert "見つかりません" in body["message"]
        data = test_client.get("/config").json()
        assert data["llm_model_path"] == ""
        assert data["whisper_model"] == "tiny"  # 検証エラー時は他の項目も保存しない

    def test_post_show_console_is_ignored(self, test_client, write_config):
        write_config()
        resp = test_client.post("/config", json={"show_console": True})
        assert resp.status_code == 200
        assert "show_console" not in test_client.get("/config").json()

    def test_wizard_save_does_not_expose_show_console(self, test_client, write_config):
        write_config({"show_console": True})
        assert test_client.post("/wizard/save", json={"whisper_model": "turbo"}).status_code == 200
        assert "show_console" not in test_client.get("/config").json()


class TestGetLlamacppClient:
    def test_same_path_returns_same_client(self, tmp_path):
        model = tmp_path / "a.gguf"
        c1 = local_llm.get_llamacpp_client(str(model))
        c2 = local_llm.get_llamacpp_client(str(model))
        assert c1 is c2

    def test_changing_path_recreates_client(self, tmp_path):
        c1 = local_llm.get_llamacpp_client(str(tmp_path / "a.gguf"))
        c2 = local_llm.get_llamacpp_client(str(tmp_path / "b.gguf"))
        assert c1 is not c2
        assert c2.model_path == Path(tmp_path / "b.gguf")

    def test_none_uses_default_model_path(self, monkeypatch, tmp_path):
        default = tmp_path / "default.gguf"
        monkeypatch.setattr(local_llm, "LLAMACPP_MODEL_PATH", default)
        client = local_llm.get_llamacpp_client(None)
        assert client.model_path == default
        # 既定→カスタム→既定で毎回正しいパスに切り替わる
        custom = local_llm.get_llamacpp_client(str(tmp_path / "c.gguf"))
        assert custom.model_path == tmp_path / "c.gguf"
        assert local_llm.get_llamacpp_client(None).model_path == default


class TestLlmModelWiring:
    """設定の llm_model_path が実際の生成呼び出しに渡ること"""

    def _generate(self, test_client):
        from src_backends import main as main_mod
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "生成結果"
        resp = test_client.post("/process", json={"files": [], "text_input": "テスト入力"})
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "ok"
        return main_mod

    def test_custom_model_path_is_passed_and_default_download_skipped(self, test_client, write_config, tmp_path):
        model = tmp_path / "custom.gguf"
        model.write_text("x")
        write_config({"llm_model_path": str(model)})
        main_mod = self._generate(test_client)
        main_mod.local_llm.get_llamacpp_client.assert_called_with(str(model), "auto")
        main_mod.local_llm.ensure_model_downloaded.assert_not_called()

    def test_default_model_is_ensured_before_generation(self, test_client, write_config):
        """任意モデル指定から既定へ戻した直後でも、既定モデルが未取得なら生成前に取得する"""
        write_config({"llm_model_path": ""})
        main_mod = self._generate(test_client)
        main_mod.local_llm.ensure_model_downloaded.assert_called_once()
        main_mod.local_llm.get_llamacpp_client.assert_called_with(None, "auto")
