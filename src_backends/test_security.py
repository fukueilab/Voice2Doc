"""Security tests for Voice2Doc - path traversal, API key, upload safety."""
import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestPathTraversal:
    def test_delete_file_traversal_dotdot(self, test_client, isolate_config):
        resp = test_client.delete("/files/../../etc/passwd")
        assert resp.status_code in (400, 404, 405)

    def test_hide_history_traversal(self, test_client, sample_history_file, isolate_config):
        sample_history_file()
        resp = test_client.delete("/history/../../secret.md")
        assert resp.status_code in (400, 404, 405)

    def test_delete_history_file_traversal(self, test_client, sample_history_file, isolate_config):
        sample_history_file()
        resp = test_client.delete("/history/../../etc/passwd/file")
        assert resp.status_code in (400, 404, 405)

    def test_download_traversal_rejected(self, test_client, sample_history_file):
        sample_history_file()
        resp = test_client.post("/history/../../etc/passwd/download")
        assert resp.status_code in (400, 404, 405)

    def test_upload_traversal_filename_sanitized(self, test_client, write_config, isolate_config):
        write_config()
        resp = test_client.post(
            "/upload",
            files=[("files", ("../../evil.txt", b"bad", "text/plain"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) == 0
        assert len(data["uploaded"]) == 1
        assert data["uploaded"][0] == "evil.txt"
        assert not (isolate_config["input"] / ".." / ".." / "evil.txt").exists()

    def test_upload_dotdot_in_name_sanitized(self, test_client, write_config, isolate_config):
        write_config()
        resp = test_client.post(
            "/upload",
            files=[("files", ("..\\..\\evil.txt", b"bad", "text/plain"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) == 0
        assert len(data["uploaded"]) == 1
        assert data["uploaded"][0] == "evil.txt"

    def test_delete_file_with_dotdot_rejected(self, test_client, isolate_config):
        resp = test_client.delete("/files/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404, 405)


class TestUploadSecurity:
    def test_unsupported_extension_rejected(self, test_client, write_config):
        write_config()
        resp = test_client.post(
            "/upload",
            files=[("files", ("malware.exe", b"MZ", "application/octet-stream"))],
        )
        data = resp.json()
        assert len(data["errors"]) > 0

    def test_valid_audio_accepted(self, test_client, write_config):
        write_config()
        resp = test_client.post(
            "/upload",
            files=[("files", ("voice.wav", b"RIFF", "audio/wav"))],
        )
        data = resp.json()
        assert "voice.wav" in data["uploaded"]

    def test_multiple_files_mixed(self, test_client, write_config):
        write_config()
        resp = test_client.post(
            "/upload",
            files=[
                ("files", ("good.wav", b"RIFF", "audio/wav")),
                ("files", ("bad.exe", b"MZ", "application/octet-stream")),
            ],
        )
        data = resp.json()
        assert "good.wav" in data["uploaded"]
        assert len(data["errors"]) > 0

    def test_oversized_file_rejected(self, test_client, write_config):
        write_config()
        big_data = b"\x00" * (101 * 1024 * 1024)
        resp = test_client.post(
            "/upload",
            files=[("files", ("huge.wav", big_data, "audio/wav"))],
        )
        data = resp.json()
        assert len(data["errors"]) > 0
        assert "大きすぎます" in data["errors"][0]["error"]
