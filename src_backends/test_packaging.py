"""配布物（PyInstaller）に含めるファイルのテスト"""
from pathlib import Path

SPEC = (Path(__file__).resolve().parent.parent / "voice2doc.spec").read_text(encoding="utf-8")


def test_whisper_data_files_are_bundled():
    # openai-whisper（予備の文字起こし）は whisper/assets のメルフィルターと語彙を実行時に読む
    assert "collect_data_files('whisper')" in SPEC


def test_whisper_assets_exist_in_the_installed_package():
    import importlib.util

    root = Path(importlib.util.find_spec("whisper").origin).parent / "assets"
    for name in ("mel_filters.npz", "multilingual.tiktoken", "gpt2.tiktoken"):
        assert (root / name).is_file(), name
