"""同じファイルを重複して入れても処理は1回にするテスト

- 画面側 dedupeFiles（Node で実行）と addFiles / startProcessing の静的検査
- POST /upload の同名重複、POST /process の重複除去
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src_backends import main as main_mod

APP_JS = (Path(__file__).resolve().parent.parent / "src" / "app.js").read_text(encoding="utf-8")


def _dedupe_source() -> str:
    m = re.search(r"// <dedupeFiles>\n(.*?)// </dedupeFiles>", APP_JS, re.S)
    assert m, "dedupeFiles の目印コメントが見つからない"
    return m.group(1)


def _run_dedupe(selected, incoming):
    """app.js から dedupeFiles だけを取り出し Node で実行する"""
    script = (
        _dedupe_source()
        + "\nconst r = dedupeFiles(%s, %s);\n" % (json.dumps(selected), json.dumps(incoming))
        + "console.log(JSON.stringify({a: r.accepted.map(f => f.id), d: r.duplicates.map(f => f.id),"
        + " n: r.nameConflicts.map(f => f.id)}));\n"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def F(id_, name="a.wav", size=10, lm=1):
    return {"id": id_, "name": name, "size": size, "lastModified": lm}


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
class TestDedupeFiles:
    def test_same_file_twice_is_one(self):
        r = _run_dedupe([F("s1")], [F("n1")])
        assert r == {"a": [], "d": ["n1"], "n": []}

    def test_same_file_dropped_together_is_one(self):
        r = _run_dedupe([], [F("n1"), F("n2")])
        assert r == {"a": ["n1"], "d": ["n2"], "n": []}

    @pytest.mark.parametrize("other", [F("x", size=11), F("x", lm=2)])
    def test_same_name_different_content_is_a_conflict(self, other):
        r = _run_dedupe([F("s1")], [other])
        assert r == {"a": [], "d": [], "n": ["x"]}

    def test_names_differing_only_in_case_are_a_conflict(self):
        r = _run_dedupe([F("s1", name="Memo.wav")], [F("x", name="memo.wav", size=99)])
        assert r == {"a": [], "d": [], "n": ["x"]}

    def test_different_name_is_accepted(self):
        r = _run_dedupe([F("s1")], [F("n1", name="b.wav")])
        assert r == {"a": ["n1"], "d": [], "n": []}

    def test_order_is_kept(self):
        r = _run_dedupe([], [F("1", name="a"), F("2", name="b"), F("3", name="c")])
        assert r["a"] == ["1", "2", "3"]


class TestFrontendWiring:
    def test_add_files_uses_dedupe_before_limits(self):
        body = APP_JS[APP_JS.index("function addFiles"):APP_JS.index("function updateFileList")]
        assert "dedupeFiles(selectedFiles" in body
        assert body.index("dedupeFiles") < body.index("MAX_FILES")
        assert "app.m082" in body and "app.m083" in body

    def test_start_processing_dedupes_before_upload(self):
        body = APP_JS[APP_JS.index("async function startProcessing"):]
        assert body.index("dedupeFiles([]") < body.index("formData.append")

    def test_failed_files_are_kept_unique(self):
        assert "lastFailedFiles.includes(r.file)" in APP_JS


class TestUploadDuplicates:
    def test_same_name_in_one_request_keeps_first_only(self, test_client, write_config, isolate_config):
        write_config()
        resp = test_client.post("/upload", files=[
            ("files", ("memo.txt", b"first", "text/plain")),
            ("files", ("memo.txt", b"second", "text/plain")),
            ("files", ("memo.txt", b"third", "text/plain")),
        ])
        data = resp.json()
        assert data["uploaded"] == ["memo.txt"]
        assert len(data["errors"]) == 2
        assert (isolate_config["input"] / "memo.txt").read_bytes() == b"first"

    def test_names_differing_only_in_case_keep_first_only(self, test_client, write_config, isolate_config):
        write_config()
        resp = test_client.post("/upload", files=[
            ("files", ("Memo.txt", b"first", "text/plain")),
            ("files", ("memo.txt", b"second", "text/plain")),
        ])
        data = resp.json()
        assert data["uploaded"] == ["Memo.txt"]
        assert len(data["errors"]) == 1

    def test_different_names_are_all_saved(self, test_client, write_config):
        write_config()
        resp = test_client.post("/upload", files=[
            ("files", ("a.txt", b"1", "text/plain")),
            ("files", ("b.txt", b"2", "text/plain")),
        ])
        assert resp.json()["uploaded"] == ["a.txt", "b.txt"]


class TestProcessDuplicates:
    @pytest.fixture
    def setup(self, test_client, write_config, isolate_config):
        write_config()
        main_mod.local_llm.get_llamacpp_client.return_value.generate_report_stream.return_value = "レポート"
        for name in ("a.txt", "b.txt"):
            (isolate_config["input"] / name).write_text("本文", encoding="utf-8")
        return test_client

    def test_duplicates_are_processed_once_in_order(self, setup, monkeypatch):
        seen = []
        orig = main_mod._process_one_file

        async def spy(filename, i, total, *args, **kwargs):
            seen.append((filename, i, total))
            return await orig(filename, i, total, *args, **kwargs)

        monkeypatch.setattr(main_mod, "_process_one_file", spy)
        resp = setup.post("/process", json={"files": ["b.txt", "a.txt", "b.txt", "b.txt"]})
        assert resp.status_code == 200
        assert seen == [("b.txt", 0, 2), ("a.txt", 1, 2)]
        assert len(resp.json()["results"]) == 2

    def test_case_only_difference_is_deduplicated(self, setup, monkeypatch):
        seen = []
        orig = main_mod._process_one_file

        async def spy(filename, i, total, *args, **kwargs):
            seen.append(filename)
            return await orig(filename, i, total, *args, **kwargs)

        monkeypatch.setattr(main_mod, "_process_one_file", spy)
        setup.post("/process", json={"files": ["a.txt", "A.TXT"]})
        assert seen == ["a.txt"]

    def test_no_duplicates_unchanged(self, setup):
        resp = setup.post("/process", json={"files": ["a.txt", "b.txt"]})
        assert [r["file"] for r in resp.json()["results"]] == ["a.txt", "b.txt"]
