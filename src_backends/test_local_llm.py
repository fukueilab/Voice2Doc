"""Unit tests for local_llm.py (llama-cpp-python バックエンド)"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
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


class TestModelDownloadWithHuggingfaceHub:
    """本物の huggingface_hub で、手元の模擬サーバーからモデルを取得する（外部には接続しない）"""

    DATA = b"model-bytes" * 1000
    # huggingface_hub は import のときに環境変数を読むので、別のプロセスで動かす
    SCRIPT = """
import json, pathlib, sys, warnings
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    from src_backends import local_llm
    local_llm.CACHE_DIR = pathlib.Path(sys.argv[1])
    local_llm.LLAMACPP_MODEL_PATH = local_llm.CACHE_DIR / local_llm.LLAMACPP_MODEL_FILENAME
    path = local_llm.ensure_llamacpp_model_downloaded()
print(json.dumps({"path": str(path), "warnings": [str(w.message) for w in caught if "local_dir" in str(w.message)]}))
"""

    @pytest.fixture
    def server(self):
        data, requests = self.DATA, []
        digest = hashlib.sha256(data).hexdigest()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.command, self.path.split("?")[0], self.headers.get("User-Agent", "")))
                self.send_response(200)
                for name, value in {"Content-Length": len(data), "ETag": f'"{digest}"', "X-Linked-Size": len(data),
                                    "X-Linked-ETag": f'"{digest}"', "X-Repo-Commit": "a" * 40}.items():
                    self.send_header(name, str(value))
                self.end_headers()
                if self.command == "GET":
                    self.wfile.write(data)

            do_HEAD = do_GET

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield httpd.server_port, requests
        httpd.shutdown()
        httpd.server_close()

    def test_only_the_model_file_is_requested(self, server, tmp_path):
        port, requests = server
        env = {k: v for k, v in os.environ.items() if not k.upper().endswith(("_PROXY", "TELEMETRY", "DO_NOT_TRACK"))
               and not k.startswith("HF_")}
        env.update(HF_ENDPOINT=f"http://127.0.0.1:{port}", HF_HOME=str(tmp_path / "hf"), NO_PROXY="127.0.0.1",
                   USERPROFILE=str(tmp_path), LOCALAPPDATA=str(tmp_path / "local"), AI_AGENT="test", PYTHONUTF8="1")
        result = subprocess.run(
            [sys.executable, "-c", self.SCRIPT, str(tmp_path / "cache")], env=env, capture_output=True, text=True,
            encoding="utf-8", cwd=Path(__file__).resolve().parent.parent, timeout=120)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout.strip().splitlines()[-1])

        assert Path(out["path"]) == tmp_path / "cache" / local_llm.LLAMACPP_MODEL_FILENAME
        assert Path(out["path"]).read_bytes() == self.DATA
        assert out["warnings"] == []
        # モデルのファイル以外は取得せず、利用環境の情報（PyTorch の版・AI ツール名）も付けない
        model = f"/{local_llm.LLAMACPP_MODEL_REPO}/resolve/main/{local_llm.LLAMACPP_MODEL_FILENAME}"
        assert {path for _, path, _ in requests} == {model}
        assert not [ua for _, _, ua in requests if "torch/" in ua or "agent/" in ua]


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
