"""Unit tests for local_llm.py (llama-cpp-python バックエンド)"""
from unittest.mock import MagicMock, patch

import pytest

from src_backends import local_llm


@pytest.fixture(autouse=True)
def _reset_local_llm_globals(monkeypatch):
    """_llama_cpp_available/_llamacpp_client のキャッシュがテスト間で漏れないようにする"""
    monkeypatch.setattr(local_llm, "_llama_cpp_available", None)
    monkeypatch.setattr(local_llm, "_llamacpp_client", None)


class TestIsLlamaCppAvailable:
    def test_caches_result(self, monkeypatch):
        monkeypatch.setattr(local_llm, "_llama_cpp_available", False)
        assert local_llm.is_llama_cpp_available() is False


class TestEnsureLlamacppModelDownloaded:
    def test_existing_model_skips_download(self, tmp_path, monkeypatch):
        model_path = tmp_path / "model.gguf"
        model_path.write_text("x")
        monkeypatch.setattr(local_llm, "LLAMACPP_MODEL_PATH", model_path)

        progress = []
        result = local_llm.ensure_llamacpp_model_downloaded(lambda msg, pct=None: progress.append(msg))
        assert result == model_path
        assert any("完了" in m for m in progress)

    def test_download_success_moves_file(self, tmp_path, monkeypatch):
        model_path = tmp_path / "cache" / "model.gguf"
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(local_llm, "LLAMACPP_MODEL_PATH", model_path)
        monkeypatch.setattr(local_llm, "CACHE_DIR", cache_dir)

        downloaded_elsewhere = tmp_path / "hf_cache" / "model.gguf"
        downloaded_elsewhere.parent.mkdir(parents=True)
        downloaded_elsewhere.write_text("model-bytes")

        fake_hf_hub_download = MagicMock(return_value=str(downloaded_elsewhere))
        fake_module = MagicMock(hf_hub_download=fake_hf_hub_download)
        with patch.dict("sys.modules", {"huggingface_hub": fake_module}):
            result = local_llm.ensure_llamacpp_model_downloaded()

        assert result == model_path
        assert model_path.exists()

    def test_download_failure_raises(self, tmp_path, monkeypatch):
        model_path = tmp_path / "cache" / "model.gguf"
        monkeypatch.setattr(local_llm, "LLAMACPP_MODEL_PATH", model_path)
        monkeypatch.setattr(local_llm, "CACHE_DIR", tmp_path / "cache")

        fake_module = MagicMock(hf_hub_download=MagicMock(side_effect=RuntimeError("network error")))
        with patch.dict("sys.modules", {"huggingface_hub": fake_module}):
            with pytest.raises(RuntimeError, match="network error"):
                local_llm.ensure_llamacpp_model_downloaded()


class TestLlamaCppClient:
    def test_load_model_raises_when_llama_cpp_unavailable(self, monkeypatch):
        client = local_llm.LlamaCppClient(model_path="C:/fake/model.gguf")
        monkeypatch.setattr(local_llm, "is_llama_cpp_available", lambda: False)
        with pytest.raises(ImportError):
            client._load_model()

    def test_load_model_raises_when_model_file_missing(self, tmp_path, monkeypatch):
        client = local_llm.LlamaCppClient(model_path=str(tmp_path / "missing.gguf"))
        monkeypatch.setattr(local_llm, "is_llama_cpp_available", lambda: True)
        with pytest.raises(FileNotFoundError):
            client._load_model()

    def test_generate_report_returns_content(self, tmp_path):
        model_file = tmp_path / "model.gguf"
        model_file.write_text("x")
        client = local_llm.LlamaCppClient(model_path=str(model_file))

        fake_model = MagicMock()
        fake_model.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "生成された報告書"}}]
        }
        client._model = fake_model  # モデル読み込み済みの状態を直接構築

        result = client.generate_report("文字起こし", "test.wav")
        assert result == "生成された報告書"

    def test_generate_report_stream_yields_chunks(self, tmp_path):
        model_file = tmp_path / "model.gguf"
        model_file.write_text("x")
        client = local_llm.LlamaCppClient(model_path=str(model_file))

        fake_model = MagicMock()
        fake_model.create_chat_completion.return_value = iter([
            {"choices": [{"delta": {"content": "A"}}]},
            {"choices": [{"delta": {"content": "B"}}]},
        ])
        client._model = fake_model

        chunks = []
        result = client.generate_report_stream("文字起こし", "test.wav", on_chunk=lambda t: chunks.append(t))
        assert result == "AB"
        assert chunks == ["A", "AB"]

    def test_generate_report_stream_stops_when_cancelled(self, tmp_path):
        model_file = tmp_path / "model.gguf"
        model_file.write_text("x")
        client = local_llm.LlamaCppClient(model_path=str(model_file))

        fake_model = MagicMock()
        fake_model.create_chat_completion.return_value = iter([
            {"choices": [{"delta": {"content": "A"}}]},
            {"choices": [{"delta": {"content": "B"}}]},
        ])
        client._model = fake_model

        result = client.generate_report_stream(
            "文字起こし", "test.wav", is_cancelled=lambda: True
        )
        assert result == ""


class TestGenerateReport:
    def test_success_uses_llamacpp(self, monkeypatch):
        monkeypatch.setattr(local_llm, "is_llama_cpp_available", lambda: True)
        fake_client = MagicMock()
        fake_client.generate_report.return_value = "default-result"
        monkeypatch.setattr(local_llm, "get_llamacpp_client", lambda model_path=None, device="auto": fake_client)
        result = local_llm.generate_report("文字起こし", "test.wav")
        assert result == "default-result"

    def test_llamacpp_error_propagates(self, monkeypatch):
        monkeypatch.setattr(local_llm, "is_llama_cpp_available", lambda: True)
        fake_client = MagicMock()
        fake_client.generate_report.side_effect = RuntimeError("model error")
        monkeypatch.setattr(local_llm, "get_llamacpp_client", lambda model_path=None, device="auto": fake_client)
        with pytest.raises(RuntimeError, match="model error"):
            local_llm.generate_report("文字起こし", "test.wav")

    def test_llamacpp_unavailable_raises(self, monkeypatch):
        monkeypatch.setattr(local_llm, "is_llama_cpp_available", lambda: False)
        with pytest.raises(RuntimeError, match="llama-cpp-pythonが利用できません"):
            local_llm.generate_report("文字起こし", "test.wav")


class TestGetClient:
    def test_returns_llamacpp_client_singleton(self):
        client1 = local_llm.get_llamacpp_client()
        client2 = local_llm.get_llamacpp_client()
        assert client1 is client2
        assert isinstance(client1, local_llm.LlamaCppClient)
