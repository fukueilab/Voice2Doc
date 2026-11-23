"""ローカル API の保護（Host・Origin の検査）と静的ファイル配信の範囲のテスト"""
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src_backends import main as main_mod
from src_backends import startup
from src_backends.main import app, configure_cors

BASE = f"http://127.0.0.1:{startup.DEFAULT_PORT}"


@pytest.fixture
def client():
    yield TestClient(app, base_url=BASE, raise_server_exceptions=False)
    configure_cors(startup.DEFAULT_PORT)


def _upload(client, headers=None):
    return client.post("/upload", headers=headers, files=[("files", ("x.txt", b"hello", "text/plain"))])


class TestHost:
    @pytest.mark.parametrize("host", ["127.0.0.1:8008", "localhost:8008", "LOCALHOST:8008"])
    def test_own_host_is_accepted(self, client, host):
        assert client.get("/health", headers={"Host": host}).status_code == 200

    @pytest.mark.parametrize("host", [
        "evil.example", "evil.example:8008", "127.0.0.1", "127.0.0.1:8009", "localhost", "127.0.0.1:8008.evil.example",
        "evil.example/127.0.0.1:8008", "127.0.0.1:8008@evil.example", "", " ", "[::1]:8008", "0.0.0.0:8008",
    ])
    def test_other_host_is_rejected(self, client, host):
        assert client.get("/health", headers={"Host": host}).status_code == 400

    def test_duplicate_host_is_rejected(self, client):
        headers = [("Host", "127.0.0.1:8008"), ("Host", "evil.example")]
        assert client.get("/health", headers=headers).status_code == 400

    def test_follows_the_chosen_port(self, client):
        configure_cors(8012)
        assert client.get("/health", headers={"Host": "127.0.0.1:8012"}).status_code == 200
        assert client.get("/health", headers={"Host": "127.0.0.1:8008"}).status_code == 400

    def test_static_files_are_protected_too(self, client):
        assert client.get("/app.js").status_code == 200
        assert client.get("/app.js", headers={"Host": "evil.example"}).status_code == 400


class TestOrigin:
    @pytest.mark.parametrize("origin", ["http://127.0.0.1:8008", "http://localhost:8008"])
    def test_own_origin_is_accepted(self, client, origin, isolate_config):
        assert _upload(client, {"Origin": origin}).status_code == 200
        assert (isolate_config["input"] / "x.txt").is_file()

    def test_no_origin_is_accepted(self, client):
        # 起動待ち・ローカルのツールは Origin を送らない
        assert client.get("/health").status_code == 200
        assert _upload(client).status_code == 200

    @pytest.mark.parametrize("origin", [
        "https://evil.example", "null", "", "http://127.0.0.1:3000", "https://127.0.0.1:8008", "http://127.0.0.1",
        "http://127.0.0.1:8008.evil.example",
    ])
    def test_other_origin_is_rejected_before_any_work(self, client, origin, isolate_config):
        assert _upload(client, {"Origin": origin}).status_code == 403
        assert not list(isolate_config["input"].iterdir())

    def test_other_origin_is_rejected_for_every_method(self, client):
        for method in ("GET", "POST", "DELETE", "OPTIONS"):
            assert client.request(method, "/health", headers={"Origin": "https://evil.example"}).status_code == 403

    def test_duplicate_origin_is_rejected(self, client):
        headers = [("Origin", "http://127.0.0.1:8008"), ("Origin", "https://evil.example")]
        assert client.get("/health", headers=headers).status_code == 403

    def test_preflight_from_own_origin_still_works(self, client):
        headers = {"Origin": "http://localhost:8008", "Access-Control-Request-Method": "POST"}
        resp = client.options("/upload", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:8008"


class TestFetchMetadata:
    @pytest.mark.parametrize("site", ["cross-site", "same-site"])
    def test_write_without_origin_from_another_site_is_rejected(self, client, site, isolate_config):
        assert _upload(client, {"Sec-Fetch-Site": site}).status_code == 403
        assert not list(isolate_config["input"].iterdir())

    @pytest.mark.parametrize("site", ["cross-site", "same-site"])
    @pytest.mark.parametrize("mode", ["no-cors", "cors", "navigate-x"])
    def test_embedding_from_another_site_is_rejected(self, client, site, mode):
        # 外部ページの <img src="http://127.0.0.1:8008/..."> など（Origin が付かない）
        assert client.get("/app.js", headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": mode}).status_code == 403

    def test_framing_from_another_site_is_rejected(self, client):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"}
        assert client.get("/", headers=headers).status_code == 403

    @pytest.mark.parametrize("site", ["none", "cross-site", "same-origin"])
    def test_window_navigation_is_accepted(self, client, site):
        # 起動画面（about:blank 等）から本画面への切り替えを止めない
        headers = {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        assert client.get("/", headers=headers).status_code == 200

    def test_write_by_navigation_from_another_site_is_rejected(self, client):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        assert client.post("/safe-render/reset", headers=headers).status_code == 403

    def test_own_page_is_accepted(self, client):
        headers = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
        assert client.get("/app.js", headers=headers).status_code == 200
        assert _upload(client, {"Sec-Fetch-Site": "same-origin"}).status_code == 200


class TestMiddlewareOrder:
    def test_guard_is_outermost_and_not_stacked(self):
        from src_backends.local_guard import LocalGuard

        try:
            configure_cors(8010)
            configure_cors(8011)
            classes = [m.cls for m in app.user_middleware]
            assert classes[0] is LocalGuard
            assert classes.count(LocalGuard) == 1
        finally:
            configure_cors(startup.DEFAULT_PORT)


class TestApiDocs:
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
    def test_generated_docs_are_off(self, client, path):
        # 外部(CDN)のスクリプトを、アプリと同じオリジンで読み込まない
        text = client.get(path).text
        assert "cdn.jsdelivr.net" not in text and '"openapi"' not in text


class TestStaticFiles:
    def test_normal_files(self, client):
        assert "markdownTable" in client.get("/app.js").text
        assert "<html" in client.get("/").text

    def test_unknown_path_returns_index(self, client):
        assert "<html" in client.get("/no/such/page").text

    @pytest.mark.parametrize("path", [
        "../src_backends/config.py", "../CLAUDE.md", "../../x", "a/../../src_backends/config.py", "a/../app.js", "..",
        "..\\src_backends\\config.py", "\\\\server.invalid\\share\\x", "//server.invalid/share/x", "/windows/win.ini",
        "C:/windows/win.ini", "C:x", "app.js:stream", "\\??\\C:\\x", "a\0b",
    ])
    @pytest.mark.asyncio
    async def test_path_outside_is_rejected_without_touching_it(self, path, monkeypatch):
        # UNC パスは開くだけで外部へ接続しうるため、ファイルシステムに触れる前に拒否する
        def touched(self, *args, **kwargs):
            raise AssertionError(f"touched: {self}")

        with monkeypatch.context() as mp, pytest.raises(main_mod.HTTPException) as e:
            for name in ("resolve", "exists", "is_file", "stat"):
                mp.setattr(Path, name, touched)
            await main_mod.serve_frontend(path)
        assert e.value.status_code == 403

    @pytest.mark.parametrize("url", [
        "/..%2fsrc_backends/config.py", "/%2e%2e%2fsrc_backends%2fconfig.py", "/..%5csrc_backends%5cconfig.py",
        "/%5c%5cserver.invalid%5cshare%5cx", "/%2f%2fserver.invalid/share/x",
    ])
    def test_encoded_path_is_rejected(self, client, url):
        resp = client.get(url)
        assert resp.status_code == 403
        assert "APP_VERSION" not in resp.text

    def test_sibling_folder_with_the_same_prefix_is_rejected(self, tmp_path, monkeypatch, client):
        # 成分の検査を抜けても、解決後の場所が src の外なら返さない（リンクなど）
        (tmp_path / "src").mkdir()
        (tmp_path / "src2").mkdir()
        (tmp_path / "src2" / "secret.txt").write_text("secret")
        monkeypatch.setattr(main_mod, "STATIC_DIR", tmp_path / "src")
        real = Path.resolve
        monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: real(
            tmp_path / "src2" / "secret.txt" if self.name == "link.txt" else self, *a, **k))
        resp = client.get("/link.txt")
        assert resp.status_code == 403
        assert "secret" not in resp.text
