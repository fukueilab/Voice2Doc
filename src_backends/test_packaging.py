"""配布物（PyInstaller）に含めるファイルと、同梱する依存のテスト"""
import re
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "voice2doc.spec").read_text(encoding="utf-8")


def test_starlette_is_pinned_to_a_version_without_known_vulnerabilities():
    # fastapi 経由の間接的な依存のままだと、Dependabot の警告に出ず、古い版が入りうる
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.search(r"^starlette==(\d+)\.(\d+)\.(\d+)$", req, re.MULTILINE)
    assert pinned and tuple(map(int, pinned.groups())) >= (1, 3, 1)


def test_fastapi_does_not_pull_in_telemetry():
    # 外部へ送信しないアプリなので、テレメトリーの部品（opentelemetry）を同梱しない
    names = {re.match(r"[\w.-]+", r).group().lower() for r in metadata.requires("fastapi") if "extra ==" not in r}
    assert "starlette" in names and "opentelemetry-api" not in names


def test_whisper_data_files_are_bundled():
    # openai-whisper（予備の文字起こし）は whisper/assets のメルフィルターと語彙を実行時に読む
    assert "collect_data_files('whisper')" in SPEC


def test_whisper_assets_exist_in_the_installed_package():
    import importlib.util

    root = Path(importlib.util.find_spec("whisper").origin).parent / "assets"
    for name in ("mel_filters.npz", "multilingual.tiktoken", "gpt2.tiktoken"):
        assert (root / name).is_file(), name
