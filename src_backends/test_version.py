"""バージョン表記（Semantic Versioning 2.0.0）とインストーラーとの整合性のテスト"""
import re
from pathlib import Path

from .config import APP_VERSION

# semver.org 公式の正規表現
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
ROOT = Path(__file__).resolve().parent.parent


def test_app_version_is_valid_semver():
    assert SEMVER_RE.match(APP_VERSION), APP_VERSION


def test_installer_version_matches_app_version():
    nsi = (ROOT / "installer" / "voice2doc_installer.nsi").read_text(encoding="utf-8-sig")
    m = re.search(r'^!define APP_VERSION "([^"]+)"', nsi, re.MULTILINE)
    assert m, "installer/voice2doc_installer.nsi に APP_VERSION の定義が見つからない"
    assert m.group(1) == APP_VERSION


def test_changelog_has_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{APP_VERSION}]" in changelog
