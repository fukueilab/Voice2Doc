"""NSISインストーラースクリプトの静的検査（ショートカットのアイコン）

インストーラー自体の実行はユーザー環境（デスクトップ・スタートメニュー）を変更するためテストでは行わず、
アイコンが確実に反映されるための記述が入っていることだけを検査する。"""
import re
from pathlib import Path

import pytest

NSI = Path(__file__).resolve().parent.parent / "installer" / "voice2doc_installer.nsi"


@pytest.fixture(scope="module")
def script() -> str:
    return NSI.read_text(encoding="utf-8-sig")


def test_icon_file_is_installed_explicitly(script):
    assert re.search(r'File /oname=\$INSTDIR\\Voice2Doc\.ico "\.\.\\assets\\icon\.ico"', script)


def test_desktop_shortcut_uses_installed_ico(script):
    line = next(ln for ln in script.splitlines() if ln.strip().startswith('CreateShortCut "$DESKTOP\\Voice2Doc.lnk"'))
    assert r'"$INSTDIR\Voice2Doc.ico" 0' in line


def test_start_menu_shortcuts_use_installed_ico(script):
    lines = [ln for ln in script.splitlines() if ln.strip().startswith('CreateShortCut "$SMPROGRAMS\\Voice2Doc\\')]
    assert len(lines) == 2
    assert all(r"$INSTDIR\Voice2Doc.ico" in ln for ln in lines)


def test_old_shortcuts_are_deleted_before_recreating(script):
    desktop_delete = script.index('Delete "$DESKTOP\\Voice2Doc.lnk"')
    desktop_create = script.index('CreateShortCut "$DESKTOP\\Voice2Doc.lnk"')
    assert desktop_delete < desktop_create


def test_shell_is_notified_to_refresh_icons(script):
    assert "SHChangeNotify(i 0x08000000" in script


def test_uninstall_display_icon_uses_ico(script):
    assert r"""'"$INSTDIR\Voice2Doc.ico"'""" in script


def test_icon_source_exists_and_has_standard_sizes():
    from PIL import Image
    ico = NSI.parent.parent / "assets" / "icon.ico"
    assert ico.is_file()
    with Image.open(ico) as im:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(im.info["sizes"])


def test_names_and_outputs_are_voice2doc(script):
    assert 'Name "Voice2Doc"' in script
    assert 'OutFile "..\\dist\\Voice2Doc-Setup.exe"' in script
    assert 'InstallDir "$LOCALAPPDATA\\Voice2Doc"' in script
    assert '!define APP_EXE "Voice2Doc.exe"' in script
    assert 'File "..\\dist\\Voice2Doc\\Voice2Doc.exe"' in script


def test_installer_has_no_old_name_handling(script):
    """旧名の引き継ぎ・旧ショートカットの整理を持たない"""
    assert "Legacy" not in script and "MSG_25" not in script


def test_vibe_is_not_bundled_by_the_installer(script):
    """vibe-server.exe・ffmpeg.exe は同梱せず、利用者がインストールする Vibe のものを使う"""
    file_lines = [ln for ln in script.splitlines() if ln.strip().startswith("File ")]
    assert file_lines and not any("vibe" in ln.lower() or "ffmpeg" in ln.lower() for ln in file_lines)


def test_finish_page_guides_to_vibe_and_offers_to_launch_the_app(script):
    """完了ページで、音声認識に Vibe を使うこと・未導入なら初回起動時に導入することを日英で示し、
    「Voice2Doc を起動する」チェック（既定はオン）を置く。Vibe のページは開かない"""
    for lang in ("JAPANESE", "ENGLISH"):
        text = re.search(rf'^LangString MSG_FINISH_VIBE \$\{{LANG_{lang}\}} "(.*)"$', script, re.M).group(1)
        assert "Vibe" in text and "Voice2Doc" in text
    ja_run = "Voice2Doc を起動する"
    en_run = "Launch Voice2Doc"
    assert f'LangString MSG_RUN_APP ${{LANG_JAPANESE}} "{ja_run}"' in script
    assert f'LangString MSG_RUN_APP ${{LANG_ENGLISH}} "{en_run}"' in script
    finish = script.index("!insertmacro MUI_PAGE_FINISH")
    for define in ('!define MUI_FINISHPAGE_TEXT "$(MSG_FINISH_VIBE)"',
                   '!define MUI_FINISHPAGE_RUN "$INSTDIR\\${APP_EXE}"',
                   '!define MUI_FINISHPAGE_RUN_TEXT "$(MSG_RUN_APP)"',
                   "!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishPageShow"):
        assert -1 < script.index(define) < finish, define
    # 既定はオン（NOTCHECKED を付けない）。Vibe のページを開く処理・検出は無い
    assert "MUI_FINISHPAGE_RUN_NOTCHECKED" not in script
    for gone in ("SHOWREADME", "OpenVibePage", "DetectVibe", "VIBE_URL", "MSG_OPEN_VIBE"):
        assert gone not in script, gone


def test_finish_text_fits_above_the_checkbox(script):
    """チェックボックスがあると本文の欄が低くなるため、欄を広げ、文を短く保つ（切れて Vibe の説明が見えなくならない）"""
    assert script.index("!define MUI_FINISHPAGE_TEXT_LARGE") < script.index("!insertmacro MUI_PAGE_FINISH")
    for lang in ("JAPANESE", "ENGLISH"):
        text = re.search(rf'^LangString MSG_FINISH_VIBE \$\{{LANG_{lang}\}} "(.*)"$', script, re.M).group(1)
        assert text.count("$\\n") <= 3 and len(text) < 220, lang


def test_finish_checkbox_label_is_one_line_and_body_is_bounded(script):
    """チェックボックスは1行分の高さしかないため、文言を1行に収める（日本語24字以内・英語36字以内）。
    本文は文字数と行数に上限を設ける。切れ防止の保険として FinishPageShow で欄を広げて動かす。"""
    limits = {"JAPANESE": (24, 130, 3), "ENGLISH": (36, 230, 3)}
    for lang, (label_max, body_max, lines_max) in limits.items():
        label = re.search(rf'^LangString MSG_RUN_APP \$\{{LANG_{lang}\}} "(.*)"$', script, re.M).group(1)
        body = re.search(rf'^LangString MSG_FINISH_VIBE \$\{{LANG_{lang}\}} "(.*)"$', script, re.M).group(1)
        assert len(label) <= label_max, (lang, len(label))
        assert "$\\n" not in label
        assert len(body) <= body_max, (lang, len(body))
        assert len(body.split("$\\n")) <= lines_max, lang
    show = _function_body(script, "FinishPageShow")
    assert "user32::MoveWindow" in show
    assert "$mui.FinishPage.Text" in show and "$mui.FinishPage.Run" in show


def test_old_program_files_are_removed_before_copying(script):
    """上書きインストールで前のバージョンの不要なファイルが残らないよう、_internal を消してからコピーする"""
    assert script.index('RMDir /r "$INSTDIR\\_internal"') < script.index('File /r "..\\dist\\Voice2Doc\\_internal"')
def test_installer_messages_are_defined_in_both_languages():
    """画面に出す文言はLangString（日本語/英語）にして、OSの言語で自動的に切り替わるようにする"""
    script = NSI.read_text(encoding="utf-8-sig")
    assert "Unicode True" in script
    assert '!insertmacro MUI_LANGUAGE "Japanese"' in script and '!insertmacro MUI_LANGUAGE "English"' in script
    names = set(re.findall(r"^LangString (\w+) \$\{LANG_JAPANESE\}", script, re.M))
    english = set(re.findall(r"^LangString (\w+) \$\{LANG_ENGLISH\}", script, re.M))
    assert names and names == english
    used = set(re.findall(r"\$\((\w+)\)", script))
    assert used <= names, used - names
    ja = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
    for line in script.splitlines():
        if line.lstrip().startswith(("DetailPrint", "MessageBox")):
            assert not ja.search(line), line
    for line in script.splitlines():
        if line.startswith("LangString") and "LANG_ENGLISH" in line:
            assert not ja.search(line), line


def _function_body(script: str, name: str) -> str:
    match = re.search(rf"^Function {re.escape(name)}\s*$(.*?)^FunctionEnd", script, re.M | re.S)
    assert match, name
    return match.group(1)


def test_language_is_chosen_from_the_os_without_a_dialog(script):
    """日本語OSなら日本語、それ以外（英語・フランス語など）はすべて英語。選択ダイアログは出さない"""
    macro = re.search(r"^!macro SelectLanguage$(.*?)^!macroend", script, re.M | re.S)
    assert macro
    body = macro.group(1)
    assert "GetUserDefaultUILanguage" in body
    assert "& 0x3FF" in body and "= 0x11" in body
    assert "StrCpy $LANGUAGE ${LANG_JAPANESE}" in body
    assert re.search(r"\$\{Else\}\s+StrCpy \$LANGUAGE \$\{LANG_ENGLISH\}", body)
    assert "MUI_LANGDLL_DISPLAY" not in script
    for name in (".onInit", "un.onInit"):
        assert "!insertmacro SelectLanguage" in _function_body(script, name), name


def test_english_is_the_fallback_language(script):
    """OSの言語に合う言語が無いとき NSIS は最初の言語を使うので、English を先に登録する"""
    assert script.index('MUI_LANGUAGE "English"') < script.index('MUI_LANGUAGE "Japanese"')


def test_license_and_third_party_notices_are_installed(script):
    """MIT の著作権表示と第三者ソフトウェアの一覧を、インストール先にも置く"""
    assert r'$INSTDIR\LICENSE.txt "..\LICENSE"' in script
    assert r'$INSTDIR\THIRD_PARTY_NOTICES.md "..\THIRD_PARTY_NOTICES.md"' in script
