"""インストーラー完了ページだけを持つ検証用 NSIS を生成・ビルドする（何もインストールしない）。

本番の voice2doc_installer.nsi から、完了ページの文言（LangString）と FinishPageShow を取り出して
そのまま使うので、本番と同じ表示になる。OS の言語は変えられないため、言語は引数で切り替える。

使い方:
    python scripts/finish_page_preview.py ja   # 日本語で起動
    python scripts/finish_page_preview.py en   # 英語で起動
    python scripts/finish_page_preview.py ja --build-only  # ビルドのみ
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "installer" / "voice2doc_installer.nsi"
MAKENSIS = r"C:\Program Files (x86)\NSIS\makensis.exe"


def _extract(text: str) -> tuple[str, str]:
    """本番スクリプトから完了ページの LangString 行と FinishPageShow 関数を取り出す。"""
    langs = "\n".join(
        line for line in text.splitlines()
        if re.match(r"LangString MSG_(FINISH_VIBE|RUN_APP) ", line)
    )
    m = re.search(r"Function FinishPageShow\n.*?\nFunctionEnd", text, re.S)
    if not m:
        raise SystemExit("FinishPageShow が見つかりません")
    return langs, m.group(0)


def build(lang: str, outdir: Path) -> Path:
    text = SRC.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    langs, finish_fn = _extract(text)
    lang_const = "${LANG_JAPANESE}" if lang == "ja" else "${LANG_ENGLISH}"
    exe = outdir / f"finish_preview_{lang}.exe"
    nsi = f"""Unicode True
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
Name "FinishPreview"
OutFile "{exe}"
RequestExecutionLevel user
!define MUI_ICON "{ROOT / 'assets' / 'icon.ico'}"
!insertmacro MUI_PAGE_WELCOME
!define MUI_FINISHPAGE_TEXT "$(MSG_FINISH_VIBE)"
!define MUI_FINISHPAGE_TEXT_LARGE
!define MUI_FINISHPAGE_RUN "$INSTDIR\\Voice2Doc.exe"
!define MUI_FINISHPAGE_RUN_TEXT "$(MSG_RUN_APP)"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishPageShow
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Japanese"
Function .onInit
    StrCpy $LANGUAGE {lang_const}
FunctionEnd
{langs}
{finish_fn}
Section "Dummy"
SectionEnd
"""
    nsi_path = outdir / f"finish_preview_{lang}.nsi"
    nsi_path.write_text("\ufeff" + nsi, encoding="utf-8")
    subprocess.run([MAKENSIS, "/V1", str(nsi_path)], check=True)
    return exe


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "ja"
    out = Path(tempfile.mkdtemp(prefix="finish_preview_"))
    exe = build(lang, out)
    print(exe)
    if "--build-only" not in sys.argv:
        subprocess.Popen([str(exe)])
