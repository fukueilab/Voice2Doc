"""POST /dialog/pick（ファイル/フォルダ選択ダイアログ）のテスト"""
from unittest.mock import MagicMock

import pytest
import webview


@pytest.fixture
def fake_window(monkeypatch):
    window = MagicMock()
    monkeypatch.setattr(webview, "windows", [window])
    return window


class TestPickDialog:
    def test_exe_returns_selected_path_with_exe_filter(self, test_client, fake_window):
        fake_window.create_file_dialog.return_value = (r"C:\Users\x\vibe\vibe-server.exe",)
        resp = test_client.post("/dialog/pick", json={"kind": "exe"})
        assert resp.json() == {"status": "ok", "path": r"C:\Users\x\vibe\vibe-server.exe"}
        args, kwargs = fake_window.create_file_dialog.call_args
        assert (args[0] if args else kwargs["dialog_type"]) == webview.FileDialog.OPEN
        assert kwargs["allow_multiple"] is False
        assert any("*.exe" in t for t in kwargs["file_types"])
        assert any("*.*" in t for t in kwargs["file_types"])  # 「すべてのファイル」も選べる

    def test_gguf_uses_gguf_filter(self, test_client, fake_window):
        fake_window.create_file_dialog.return_value = [r"D:\models\a.gguf"]
        resp = test_client.post("/dialog/pick", json={"kind": "gguf"})
        assert resp.json()["path"] == r"D:\models\a.gguf"
        kwargs = fake_window.create_file_dialog.call_args.kwargs
        assert any("*.gguf" in t for t in kwargs["file_types"])

    def test_folder_uses_folder_dialog(self, test_client, fake_window):
        fake_window.create_file_dialog.return_value = (r"C:\out",)
        resp = test_client.post("/dialog/pick", json={"kind": "folder"})
        assert resp.json() == {"status": "ok", "path": r"C:\out"}
        args, kwargs = fake_window.create_file_dialog.call_args
        assert (args[0] if args else kwargs["dialog_type"]) == webview.FileDialog.FOLDER

    def test_cancel_returns_cancel(self, test_client, fake_window):
        fake_window.create_file_dialog.return_value = None
        assert test_client.post("/dialog/pick", json={"kind": "exe"}).json() == {"status": "cancel"}
        fake_window.create_file_dialog.return_value = ()
        assert test_client.post("/dialog/pick", json={"kind": "exe"}).json() == {"status": "cancel"}

    def test_no_window_returns_error_so_user_can_type_the_path(self, test_client, monkeypatch):
        monkeypatch.setattr(webview, "windows", [])
        body = test_client.post("/dialog/pick", json={"kind": "exe"}).json()
        assert body["status"] == "error"
        assert "直接入力" in body["message"]

    def test_unknown_kind_is_rejected(self, test_client, fake_window):
        assert test_client.post("/dialog/pick", json={"kind": "save"}).status_code == 400
        fake_window.create_file_dialog.assert_not_called()

    def test_dialog_exception_is_reported_not_raised(self, test_client, fake_window):
        fake_window.create_file_dialog.side_effect = RuntimeError("boom")
        body = test_client.post("/dialog/pick", json={"kind": "exe"}).json()
        assert body["status"] == "error"

    def test_initial_existing_file_opens_its_folder(self, test_client, fake_window, tmp_path):
        target = tmp_path / "vibe-server.exe"
        target.write_text("x")
        fake_window.create_file_dialog.return_value = None
        test_client.post("/dialog/pick", json={"kind": "exe", "initial": str(target)})
        assert fake_window.create_file_dialog.call_args.kwargs["directory"] == str(tmp_path)

    def test_initial_existing_folder_is_used_as_is(self, test_client, fake_window, tmp_path):
        fake_window.create_file_dialog.return_value = None
        test_client.post("/dialog/pick", json={"kind": "folder", "initial": str(tmp_path)})
        assert fake_window.create_file_dialog.call_args.kwargs["directory"] == str(tmp_path)

    def test_initial_missing_or_quoted_path_falls_back_to_empty(self, test_client, fake_window, tmp_path):
        fake_window.create_file_dialog.return_value = None
        test_client.post("/dialog/pick", json={"kind": "gguf", "initial": str(tmp_path / "nope" / "x.gguf")})
        assert fake_window.create_file_dialog.call_args.kwargs["directory"] == ""
        test_client.post("/dialog/pick", json={"kind": "gguf", "initial": "gemma4:e2b-qat(llama.cpp)"})
        assert fake_window.create_file_dialog.call_args.kwargs["directory"] == ""


class TestFileFilters:
    @pytest.mark.parametrize("kind", ["exe", "gguf", "folder"])
    def test_all_file_filters_are_accepted_by_pywebview(self, kind):
        """pywebviewは説明文を ^([\\w ]+) で検査する（ハイフン等は不可）。通らないと実機では必ず失敗する"""
        from webview.util import parse_file_type

        from src_backends import main as main_mod
        for lang in ("ja", "en"):
            main_mod.i18n.set_language(lang)
            for file_type in main_mod._dialog_file_types(kind):
                parse_file_type(file_type)  # 例外にならないこと（どちらの言語でも）
