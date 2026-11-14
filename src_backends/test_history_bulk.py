"""処理履歴の一括操作（チェックボックスで選んだ複数件の履歴削除・ファイル削除）のテスト"""
import pytest


@pytest.fixture
def output(isolate_config):
    out = isolate_config["output"]
    for stem in ("a", "b", "c"):
        (out / f"{stem}.md").write_text(f"# {stem}の題\n本文", encoding="utf-8")
    (out / "a.txt").write_text("aの文字起こし", encoding="utf-8")
    (out / "b.txt").write_text("bの文字起こし", encoding="utf-8")
    return out


def _ids(client):
    return {item["name"]: item["id"] for item in client.get("/history").json()["items"]}


class TestHistoryIds:
    def test_items_include_id_made_of_dir_and_name(self, test_client, write_config, output):
        write_config()
        items = test_client.get("/history").json()["items"]
        assert {i["name"] for i in items} == {"a.md", "b.md", "c.md"}
        for item in items:
            assert item["id"] == f"{item['dir_path']}::{item['name']}"


class TestBulkHide:
    def test_selected_items_are_hidden_and_files_remain(self, test_client, write_config, output):
        write_config()
        ids = _ids(test_client)
        resp = test_client.post("/history/hide", json={"ids": [ids["a.md"], ids["b.md"]]})
        assert resp.status_code == 200
        assert resp.json()["hidden_count"] == 2
        assert set(_ids(test_client)) == {"c.md"}
        assert (output / "a.md").exists() and (output / "a.txt").exists()  # ファイルは消さない

    def test_already_hidden_and_missing_are_skipped(self, test_client, write_config, output):
        write_config()
        ids = _ids(test_client)
        test_client.post("/history/hide", json={"ids": [ids["a.md"]]})
        body = test_client.post("/history/hide", json={
            "ids": [ids["a.md"], f"{output}::nothing.md"]}).json()
        assert body["hidden_count"] == 0 and body["skipped"] == 2

    @pytest.mark.parametrize("bad", [
        "no-separator.md",
        "::a.md",
        "C:\\elsewhere::a.md",            # 走査対象外のフォルダ
        "OUTPUT::..\\secret.md",          # パストラバーサル
        "OUTPUT::sub/a.md",
        "OUTPUT::a.txt",                  # .md 以外
    ])
    def test_invalid_ids_are_rejected_and_nothing_changes(self, test_client, write_config, output, bad):
        write_config()
        ids = _ids(test_client)
        bad = bad.replace("OUTPUT", str(output))
        resp = test_client.post("/history/hide", json={"ids": [ids["a.md"], bad]})
        assert resp.status_code == 400
        assert set(_ids(test_client)) == {"a.md", "b.md", "c.md"}  # 正しいidも処理されない

    def test_empty_ids_is_rejected(self, test_client, write_config, output):
        write_config()
        assert test_client.post("/history/hide", json={"ids": []}).status_code == 400


class TestBulkDeleteFiles:
    def test_md_and_matching_txt_are_deleted_together(self, test_client, write_config, output):
        write_config()
        ids = _ids(test_client)
        resp = test_client.post("/history/delete-files", json={"ids": [ids["a.md"], ids["c.md"]]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted_count"] == 2
        assert body["txt_deleted_count"] == 1  # a.txt のみ（c.txt は無い）
        assert not (output / "a.md").exists() and not (output / "a.txt").exists()
        assert not (output / "c.md").exists()
        assert (output / "b.md").exists() and (output / "b.txt").exists()  # 選ばなかったものは残る

    def test_hidden_record_is_cleaned_so_a_new_file_with_same_name_shows(self, test_client, write_config, output):
        write_config()
        ids = _ids(test_client)
        test_client.post("/history/hide", json={"ids": [ids["a.md"]]})
        test_client.post("/history/delete-files", json={"ids": [ids["a.md"]]})
        (output / "a.md").write_text("# 新しいa\n本文", encoding="utf-8")
        assert "a.md" in _ids(test_client)

    def test_missing_file_is_skipped_not_an_error(self, test_client, write_config, output):
        write_config()
        body = test_client.post("/history/delete-files", json={"ids": [f"{output}::gone.md"]}).json()
        assert body["deleted_count"] == 0 and body["skipped"] == 1

    def test_invalid_id_deletes_nothing(self, test_client, write_config, output):
        write_config()
        ids = _ids(test_client)
        resp = test_client.post("/history/delete-files", json={"ids": [ids["a.md"], f"{output}::..\\x.md"]})
        assert resp.status_code == 400
        assert (output / "a.md").exists()

    def test_same_name_in_two_folders_is_distinguished_by_id(self, test_client, write_config, output, tmp_path,
                                                             sample_config_data):
        custom = tmp_path / "custom-out"
        custom.mkdir()
        (custom / "a.md").write_text("# カスタム側のa\n本文", encoding="utf-8")
        (custom / "a.txt").write_text("カスタム側の文字起こし", encoding="utf-8")
        data = dict(sample_config_data)
        data["saved_prompts"] = {"p": {"content": "x", "description": "", "output_dir": str(custom),
                                       "include_transcript": False}}
        data["active_prompt_name"] = "p"
        write_config(data)
        items = test_client.get("/history").json()["items"]
        custom_id = next(i["id"] for i in items if i["dir_path"] == str(custom) and i["name"] == "a.md")
        test_client.post("/history/delete-files", json={"ids": [custom_id]})
        assert not (custom / "a.md").exists() and not (custom / "a.txt").exists()
        assert (output / "a.md").exists() and (output / "a.txt").exists()  # 同名の別フォルダ側は無傷


class TestSingleFileDeleteAlsoRemovesTxt:
    def test_delete_history_file_removes_sibling_txt(self, test_client, write_config, output):
        write_config()
        assert test_client.delete("/history/a.md/file").status_code == 200
        assert not (output / "a.md").exists() and not (output / "a.txt").exists()
        assert (output / "b.txt").exists()


class TestBulkHideReshown:
    def test_file_updated_after_hiding_can_be_hidden_again(self, test_client, write_config, output):
        """非表示後に更新されて再表示された項目も、一括の「履歴削除」で再び隠せる"""
        import os
        import time
        write_config()
        ids = _ids(test_client)
        test_client.post("/history/hide", json={"ids": [ids["a.md"]]})
        time.sleep(0.05)
        os.utime(output / "a.md", None)  # 非表示にした後の更新（現在時刻）
        assert "a.md" in _ids(test_client)
        body = test_client.post("/history/hide", json={"ids": [ids["a.md"]]}).json()
        assert body["hidden_count"] == 1
        assert "a.md" not in _ids(test_client)
