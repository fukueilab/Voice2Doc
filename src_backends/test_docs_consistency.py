"""公開するファイルが現在のプログラムと食い違っていないか、公開前の名前・使っていない仕組みが残っていないかの検査

公開リポジトリは初回公開版（1.0.0-beta.3）からの1コミットで始まる。公開前の英語名・音声認識サーバーの旧名・クラウドLLM・
外部LLMサーバー連携・古い初期プロンプトの名前は、公開するファイル（git ls-files）のどこにも書かない。
（この検査ファイル自体は、探す語を持つため対象外）"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SELF = "src_backends/test_docs_consistency.py"
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".ico", ".ttf", ".gguf", ".bin", ".exe", ".wav", ".mp3", ".mp4"}
USER_DOCS = [
    "README.md", "README.ja.md", "SECURITY.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md",
    "docs/install-guide.md", "docs/install-guide.en.md", ".github/ISSUE_TEMPLATE/bug_report.yml",
]
# 公開しない作業メモ・過去の仕様（作業ブランチの履歴にだけ残す）
PRIVATE_FILES = ["docs/improvement-plan.md", "docs/claude-workflow-notes.md", "docs/next-session-handoff.md",
                 "docs/next-session-handoff-p4.md", "issue.md"]
PUBLIC_SPECS = {"docs/spec/voice2doc.md"}
# 開発者だけが使う指示書は作業ブランチにだけある（公開コミットには入れない）。無いときは検査を省く
DEV_DOCS = [f for f in ["CLAUDE" + ".md"] if (ROOT / f).is_file()]

# 公開しない語（文字列を分けて書き、他の検査でこのファイルが引っかからないようにしている）
FORBIDDEN = re.compile("|".join([
    "speak" + "write", "speak" + "2doc", "speac" + "write", "voice" + "dock", "voice2d" + "doc", "voice2" + "obsidian",
    "j66" + "184", r"c:\\users\\[a-z0-9]{2,}", r"(?<![a-z])" + "so" + "na", "ol" + "lama", "google ai " + "studio",
    "日報",  # 古い初期プロンプトの名前
    r"0\.1\.0-beta",  # 公開前の内部開発版の番号
]), re.I)
REMOVED_FEATURES = re.compile(
    r"google ai|ai\.google\.dev/gemini|dpapi|api ?key|apiキー|クラウド|cloud llm|\.env\.example", re.I)


def _public_files() -> list:
    """公開するファイル（追跡中 + まだ追加していない新規ファイル。.gitignore の対象は除く）"""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return sorted({f for f in out.splitlines() if (ROOT / f).is_file()})


PUBLIC = _public_files()


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


def test_public_files_have_no_old_names_or_retired_features():
    offenders = []
    for name in PUBLIC:
        if name == SELF or Path(name).suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = _text(name)
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{name}:{number}: {line.strip()[:80]}")
    assert offenders == []


def test_private_notes_are_not_published():
    assert [f for f in PRIVATE_FILES if f in PUBLIC] == []
    assert {f for f in PUBLIC if f.startswith("docs/spec/")} == PUBLIC_SPECS


def test_no_large_or_generated_files_are_published():
    """モデル・録音・ビルド成果物は公開しない（フォント以外に 2MB を超えるファイルも無い）"""
    assert [f for f in PUBLIC if f.split("/")[0] in ("dist", "build")] == []
    assert [f for f in PUBLIC if Path(f).suffix.lower() in {".gguf", ".bin", ".wav", ".mp3", ".mp4", ".m4a"}] == []
    big = [f for f in PUBLIC if (ROOT / f).stat().st_size > 2 * 1024 * 1024 and not f.startswith("fonts/")]
    assert big == []


@pytest.mark.parametrize("name", USER_DOCS)
def test_removed_features_are_not_documented(name):
    assert REMOVED_FEATURES.findall(_text(name)) == []


@pytest.mark.parametrize("name", USER_DOCS + ["spec.md", "docs/RELEASING.md"] + DEV_DOCS)
def test_no_bundled_transcription_binary_is_documented(name):
    """vibe-server.exe・ffmpeg.exe は同梱せず、Vibe のものを使う。同梱前提の実行ファイル名を書かない"""
    assert re.findall(r"(?<![-\w])vibe\.exe", _text(name), re.I) == []


def test_github_url_is_voice2doc():
    texts = {n: _text(n) for n in USER_DOCS + ["spec.md", "docs/RELEASING.md", ".github/ISSUE_TEMPLATE/config.yml"]}
    assert "https://github.com/fukueilab/Voice2Doc" in texts["README.md"] + texts["README.ja.md"]


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "docs/install-guide.md", "docs/install-guide.en.md",
                                  "CONTRIBUTING.md"])
def test_vibe_is_named_as_a_requirement(name):
    assert "https://github.com/thewh1teagle/vibe/releases" in _text(name)


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "docs/install-guide.md", "docs/install-guide.en.md",
                                  "SECURITY.md", ".github/ISSUE_TEMPLATE/bug_report.yml"] + DEV_DOCS)
def test_documents_name_the_current_version(name):
    from src_backends.config import APP_VERSION
    text = _text(name)
    assert APP_VERSION in text or APP_VERSION.replace("-", "--") in text or "1.0.0-beta.x" in text, name


def test_readmes_list_the_four_builtin_prompts():
    assert all(w in _text("README.md") for w in ("Proofreading", "Proposal", "Report", "Customer meeting notes"))
    assert all(w in _text("README.ja.md") for w in ("文章校正", "企画書", "報告書", "お客様面談記録"))


@pytest.mark.parametrize("name", ["requirements.txt", "voice2doc.spec"])
def test_unused_dependencies_are_gone(name):
    text = _text(name)
    assert "genai" not in text and "dotenv" not in text


def test_code_does_not_import_unused_modules():
    unused = re.compile(r"genai|dotenv|build_tools")
    offenders = [p.name for p in (ROOT / "src_backends").glob("*.py")
                 if not p.name.startswith("test_") and unused.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
    assert not (ROOT / "src_backends" / "build_tools.py").exists()


def test_release_workflow_builds_a_draft_release_on_tags():
    workflow = _text(".github/workflows/release.yml")
    assert 'tags: ["v*"]' in workflow and "runs-on: windows-latest" in workflow
    assert "pyinstaller voice2doc.spec" in workflow and "makensis.exe" in workflow
    assert "draft: true" in workflow and "dist/Voice2Doc-Setup.exe" in workflow


@pytest.mark.parametrize("name", ["README.md", "README.ja.md", "CONTRIBUTING.md", ".github/PULL_REQUEST_TEMPLATE.md"])
def test_contributing_note_about_response_time_and_merging(name):
    """個人開発で、お返事に時間がかかること・PRを取り込めない場合があることを、歓迎も拒否もしない中立の文言で伝える"""
    text = _text(name)
    assert ("replies may take a" in text and "may not be" in text and "merge" in text) or \
           ("お返事に時間がかかったり" in text and "取り込めない場合" in text)
    assert not re.search(r"are welcome|歓迎", text), "貢献を歓迎する文言は書かない（中立の文言にする）"


def test_changelog_has_only_the_first_public_release():
    """CHANGELOG は初回公開版の説明だけを載せる（公開前の版の履歴は載せない）"""
    from src_backends.config import APP_VERSION
    headings = re.findall(r"^## \[([^\]]+)\]", _text("CHANGELOG.md"), re.M)
    assert headings == ["Unreleased", APP_VERSION]
