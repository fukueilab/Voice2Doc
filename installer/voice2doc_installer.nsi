; ========================================
; Voice2Doc NSIS Installer Script
; ========================================

Unicode True

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"

; アプリアイコン（MUI_PAGEマクロより前に定義する必要がある）
!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"

; ========================================
; Basic Settings
; ========================================
Name "Voice2Doc"
OutFile "..\dist\Voice2Doc-Setup.exe"
InstallDir "$LOCALAPPDATA\Voice2Doc"
InstallDirRegKey HKCU "Software\Voice2Doc" "InstallDir"
RequestExecutionLevel user

; Application Info
!define APP_NAME "Voice2Doc"
!define APP_VERSION "1.0.0-beta.3"
!define APP_PUBLISHER "Fukuei Lab"
!define APP_URL "https://www.8asue.com/fukuei/"
!define APP_EXE "Voice2Doc.exe"
; 音声認識の vibe-server.exe と ffmpeg.exe は同梱せず、利用者がインストールする Vibe のものを使う

; ========================================
; Runtime Download URLs
; ========================================
!define VC_REDIST_URL "https://aka.ms/vs/17/release/vc_redist.x64.exe"
!define WEBVIEW2_URL "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

; ========================================
; Hash Verification (SHA256)
; ========================================
!define VC_REDIST_HASH "CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B"
!define WEBVIEW2_HASH "8C4A80540B6BBCBEF30A4E8C7D1AC504B6FC09DB922B4ACDFD85C9D5F6F1050E"

; ========================================
; Runtime Detection Variables
; ========================================
Var VcRedistPath
Var WebView2Path
Var VcInstalled
Var WebView2Installed
Var InstallSuccess
Var RebootRequired

; ========================================
; Installer Settings
; ========================================
BrandingText "Voice2Doc v${APP_VERSION}"

; Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
; 完了ページ: 音声認識に Vibe を使うことを案内し、「Voice2Doc を起動する」チェックを置く（既定はオン）
!define MUI_FINISHPAGE_TEXT "$(MSG_FINISH_VIBE)"
; チェックボックスがあると本文の欄が低くなり案内が切れるため、本文の欄を広げる（MUI2 Finish.nsh: 40u → 60u）
!define MUI_FINISHPAGE_TEXT_LARGE
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "$(MSG_RUN_APP)"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishPageShow
!insertmacro MUI_PAGE_FINISH

; Uninstaller Pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Languages
; English を先に登録する（NSIS は OS の言語に合う言語が無いとき、最初に登録した言語を使うため）
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Japanese"

; 表示言語は選択ダイアログを出さず OS の表示言語で決める: 日本語なら日本語、それ以外はすべて英語
; （アプリの初回ウィザードの既定と同じ規則）
!macro SelectLanguage
    System::Call 'kernel32::GetUserDefaultUILanguage() i .r0'
    IntOp $0 $0 & 0x3FF
    ${If} $0 = 0x11
        StrCpy $LANGUAGE ${LANG_JAPANESE}
    ${Else}
        StrCpy $LANGUAGE ${LANG_ENGLISH}
    ${EndIf}
!macroend

; ========================================
; Messages (Japanese / English, chosen by the OS language)
; ========================================
LangString MSG_01 ${LANG_JAPANESE} "ダウンロードに失敗しました。$\n$\nファイルが正しく保存されませんでした。$\nインターネット接続を確認して、もう一度お試しください。"
LangString MSG_01 ${LANG_ENGLISH} "The download failed.$\n$\nThe file was not saved correctly.$\nCheck your internet connection and try again."
LangString MSG_02 ${LANG_JAPANESE} "Visual C++ Redistributable をダウンロード中..."
LangString MSG_02 ${LANG_ENGLISH} "Downloading Visual C++ Redistributable..."
LangString MSG_03 ${LANG_JAPANESE} "Visual C++ Redistributable のハッシュ値を検証中..."
LangString MSG_03 ${LANG_ENGLISH} "Verifying the hash of Visual C++ Redistributable..."
LangString MSG_04 ${LANG_JAPANESE} "Visual C++ Redistributable のハッシュ検証に失敗しました。$\n$\nウェブサイトから手動でインストールしてください。"
LangString MSG_04 ${LANG_ENGLISH} "Hash verification of Visual C++ Redistributable failed.$\n$\nPlease install it manually from the website."
LangString MSG_05 ${LANG_JAPANESE} "Visual C++ Redistributable のハッシュ値が一致しません。$\n$\nファイルが改ざんされている可能性があります。$\nウェブサイトから手動でインストールしてください。"
LangString MSG_05 ${LANG_ENGLISH} "The hash of Visual C++ Redistributable does not match.$\n$\nThe file may have been tampered with.$\nPlease install it manually from the website."
LangString MSG_06 ${LANG_JAPANESE} "ハッシュ検証 OK"
LangString MSG_06 ${LANG_ENGLISH} "Hash verification OK"
LangString MSG_07 ${LANG_JAPANESE} "Visual C++ Redistributable をインストール中..."
LangString MSG_07 ${LANG_ENGLISH} "Installing Visual C++ Redistributable..."
LangString MSG_08 ${LANG_JAPANESE} "Visual C++ Redistributable インストール完了"
LangString MSG_08 ${LANG_ENGLISH} "Visual C++ Redistributable installed"
LangString MSG_09 ${LANG_JAPANESE} "Visual C++ Redistributable インストール完了（再起動が必要です）"
LangString MSG_09 ${LANG_ENGLISH} "Visual C++ Redistributable installed (a restart is required)"
LangString MSG_10 ${LANG_JAPANESE} "Visual C++ Redistributable のインストールに失敗しました（終了コード: $0）。$\n$\n手動でインストールしてください。"
LangString MSG_10 ${LANG_ENGLISH} "Installing Visual C++ Redistributable failed (exit code: $0).$\n$\nPlease install it manually."
LangString MSG_11 ${LANG_JAPANESE} "Visual C++ Redistributable のダウンロードに失敗しました。$\n$\nウェブサイトから手動でインストールしてください。"
LangString MSG_11 ${LANG_ENGLISH} "Downloading Visual C++ Redistributable failed.$\n$\nPlease install it manually from the website."
LangString MSG_12 ${LANG_JAPANESE} "Visual C++ Redistributable: 既にインストール済み"
LangString MSG_12 ${LANG_ENGLISH} "Visual C++ Redistributable: already installed"
LangString MSG_13 ${LANG_JAPANESE} "WebView2 Runtime をダウンロード中..."
LangString MSG_13 ${LANG_ENGLISH} "Downloading WebView2 Runtime..."
LangString MSG_14 ${LANG_JAPANESE} "WebView2 Runtime のハッシュ値を検証中..."
LangString MSG_14 ${LANG_ENGLISH} "Verifying the hash of WebView2 Runtime..."
LangString MSG_15 ${LANG_JAPANESE} "WebView2 Runtime のハッシュ検証に失敗しました。$\n$\nウェブサイトから手動でインストールしてください。"
LangString MSG_15 ${LANG_ENGLISH} "Hash verification of WebView2 Runtime failed.$\n$\nPlease install it manually from the website."
LangString MSG_16 ${LANG_JAPANESE} "WebView2 Runtime のハッシュ値が一致しません。$\n$\nファイルが改ざんされている可能性があります。$\nウェブサイトから手動でインストールしてください。"
LangString MSG_16 ${LANG_ENGLISH} "The hash of WebView2 Runtime does not match.$\n$\nThe file may have been tampered with.$\nPlease install it manually from the website."
LangString MSG_17 ${LANG_JAPANESE} "WebView2 Runtime をインストール中..."
LangString MSG_17 ${LANG_ENGLISH} "Installing WebView2 Runtime..."
LangString MSG_18 ${LANG_JAPANESE} "WebView2 Runtime インストール完了"
LangString MSG_18 ${LANG_ENGLISH} "WebView2 Runtime installed"
LangString MSG_19 ${LANG_JAPANESE} "WebView2 Runtime インストール完了（再起動が必要です）"
LangString MSG_19 ${LANG_ENGLISH} "WebView2 Runtime installed (a restart is required)"
LangString MSG_20 ${LANG_JAPANESE} "WebView2 Runtime: 再チェックで確認済み（終了コード: $0、実際はインストール済み）"
LangString MSG_20 ${LANG_ENGLISH} "WebView2 Runtime: confirmed by the recheck (exit code: $0, it is actually installed)"
LangString MSG_21 ${LANG_JAPANESE} "WebView2 Runtime インストールエラー（終了コード: $0）"
LangString MSG_21 ${LANG_ENGLISH} "WebView2 Runtime installation error (exit code: $0)"
LangString MSG_22 ${LANG_JAPANESE} "WebView2 Runtime のインストールに失敗しました（終了コード: $0）。$\n$\n手動でインストールしてください。$\nhttps://developer.microsoft.com/en-us/microsoft-edge/webview2/"
LangString MSG_22 ${LANG_ENGLISH} "Installing WebView2 Runtime failed (exit code: $0).$\n$\nPlease install it manually.$\nhttps://developer.microsoft.com/en-us/microsoft-edge/webview2/"
LangString MSG_23 ${LANG_JAPANESE} "WebView2 Runtime のダウンロードに失敗しました。$\n$\nウェブサイトから手動でインストールしてください。"
LangString MSG_23 ${LANG_ENGLISH} "Downloading WebView2 Runtime failed.$\n$\nPlease install it manually from the website."
LangString MSG_24 ${LANG_JAPANESE} "WebView2 Runtime: 既にインストール済み"
LangString MSG_24 ${LANG_ENGLISH} "WebView2 Runtime: already installed"
LangString MSG_26 ${LANG_JAPANESE} "Voice2Doc ファイルをインストール中..."
LangString MSG_26 ${LANG_ENGLISH} "Installing the Voice2Doc files..."
LangString MSG_27 ${LANG_JAPANESE} "インストールが完了しました。$\n$\nすべてのコンポーネントが正常にインストールされましたが、変更を完全に適用するために再起動が必要です。"
LangString MSG_27 ${LANG_ENGLISH} "Installation is complete.$\n$\nAll components were installed, but a restart is required to fully apply the changes."
LangString MSG_28 ${LANG_JAPANESE} "インストールが完了しました。$\n$\nVoice2Doc v${APP_VERSION} が正常にインストールされました。$\n$\n起動前に再起動が必要な場合があります。"
LangString MSG_28 ${LANG_ENGLISH} "Installation is complete.$\n$\nVoice2Doc v${APP_VERSION} was installed successfully.$\n$\nA restart may be required before you start it."
LangString MSG_29 ${LANG_JAPANESE} "インストールが完了しましたが、一部のコンポーネントでエラーが発生しました。$\n$\nインストールログを確認し、必要的なコンポーネントを手動でインストールしてください。$\n$\n起動前に再起動が必要な場合があります。"
LangString MSG_29 ${LANG_ENGLISH} "Installation finished, but some components failed.$\n$\nCheck the install log and install the required components manually.$\n$\nA restart may be required before you start it."
LangString MSG_30 ${LANG_JAPANESE} "アンインストールが完了しました。$\n$\nVoice2Doc が正常に削除されました。"
LangString MSG_30 ${LANG_ENGLISH} "Uninstallation is complete.$\n$\nVoice2Doc was removed successfully."
LangString MSG_FINISH_VIBE ${LANG_JAPANESE} "Voice2Doc では音声認識に Vibe を利用しています。$\nVibe が未導入の場合は、初回起動時に表示される案内に従って、Vibe をダウンロードして導入してください。"
LangString MSG_FINISH_VIBE ${LANG_ENGLISH} "Voice2Doc uses Vibe for speech recognition.$\nIf Vibe is not installed yet, download and install it by following the guidance shown when the app starts for the first time."
LangString MSG_RUN_APP ${LANG_JAPANESE} "Voice2Doc を起動する"
LangString MSG_RUN_APP ${LANG_ENGLISH} "Launch Voice2Doc"

; ========================================
; Runtime Detection Functions
; ========================================
Function DetectVCRuntime
    StrCpy $VcInstalled "0"
    ; Check registry for VC++ Runtime (HKLM for system-wide)
    ReadRegStr $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Version"
    ${If} $0 != ""
        StrCpy $VcInstalled "1"
    ${EndIf}
    ; HKCU チェック（ユーザーインストール用）
    ${If} $VcInstalled == "0"
        ReadRegStr $0 HKCU "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Version"
        ${If} $0 != ""
            StrCpy $VcInstalled "1"
        ${EndIf}
    ${EndIf}
    ; Fallback: check for vcruntime140.dll in system directory
    IfFileExists "$SYSDIR\vcruntime140.dll" 0 +2
        StrCpy $VcInstalled "1"
FunctionEnd

Function DetectWebView2
    StrCpy $WebView2Installed "0"
    ; Check registry for WebView2 Runtime (HKLM for system-wide, HKCU for user install)
    ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    ${If} $0 != ""
        StrCpy $WebView2Installed "1"
    ${EndIf}
    ; Also check HKCU for user-level WebView2 installation
    ReadRegStr $0 HKCU "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    ${If} $0 != ""
        StrCpy $WebView2Installed "1"
    ${EndIf}
    ; Fallback: check for EdgeWebView installation directory
    IfFileExists "$LOCALAPPDATA\Microsoft\EdgeWebView\Application\*" 0 +2
        StrCpy $WebView2Installed "1"
FunctionEnd

; ========================================
; Verify Downloaded File (existence)
; ========================================
Function VerifyDownload
    ; $R0 = file path
    IfFileExists "$R0" 0 VerifyDownloadFailed
    Return

VerifyDownloadFailed:
    MessageBox MB_OK|MB_ICONSTOP "$(MSG_01)"
    Abort
FunctionEnd

; ========================================
; Vibe (vibe-server.exe) の検出と完了ページ
; ========================================
; アプリ側（src_backends/vibe_client.py の find_vibe_binary）と同じ場所を見る:
; %LOCALAPPDATA%\vibe・%LOCALAPPDATA%\Programs\vibe・Vibe のアンインストール登録の InstallLocation
Function FinishPageShow
    ; 再起動が必要なとき、MUI は Run の代わりに再起動のラジオボタンを作るため、何もしない
    ${IfNot} ${RebootFlag}
        ; 保険: 本文の欄を 1.3 倍の高さに広げ、チェックボックスをその分だけ下へ動かす
        ; （文言が収まる前提だが、フォントや表示倍率の違いで本文が切れるのを防ぐ）
        System::Call '*(i,i,i,i)p.r1'
        System::Call 'user32::GetWindowRect(p $mui.FinishPage.Text, p r1)'
        System::Call 'user32::ScreenToClient(p $mui.FinishPage, p r1)'
        System::Call '*$1(i.r2, i.r3, i.r4, i.r5)'
        System::Call 'user32::GetWindowRect(p $mui.FinishPage.Text, p r1)'
        System::Call '*$1(i.r8,i.r9,i.r6,i.r7)'
        IntOp $6 $6 - $8
        IntOp $7 $7 - $9
        IntOp $8 $7 * 13
        IntOp $8 $8 / 10
        IntOp $9 $8 - $7
        System::Call 'user32::MoveWindow(p $mui.FinishPage.Text, i r2, i r3, i r6, i r8, i 1)'
        System::Call 'user32::GetWindowRect(p $mui.FinishPage.Run, p r1)'
        System::Call 'user32::ScreenToClient(p $mui.FinishPage, p r1)'
        System::Call '*$1(i.r2, i.r3, i.r4, i.r5)'
        System::Call 'user32::GetWindowRect(p $mui.FinishPage.Run, p r1)'
        System::Call '*$1(i.r4, i.r5, i.r6, i.r7)'
        IntOp $6 $6 - $4
        IntOp $7 $7 - $5
        IntOp $3 $3 + $9
        System::Call 'user32::MoveWindow(p $mui.FinishPage.Run, i r2, i r3, i r6, i r7, i 1)'
        System::Free $1
    ${EndIf}
FunctionEnd

; ========================================
; Install Section
; ========================================
Section "Install" SecInstall
    SetOutPath "$INSTDIR"
    Setoverwrite on

    ; Initialize result variables
    StrCpy $InstallSuccess "1"
    StrCpy $RebootRequired "0"

    ; ----------------------------------------
    ; VC++ Runtime Installation
    ; ----------------------------------------
    ${If} $VcInstalled == "0"
        DetailPrint "$(MSG_02)"
        nsExec::ExecToStack 'powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri \'${VC_REDIST_URL}\' -OutFile \'$VcRedistPath\'"'
        Pop $0
        ${If} $0 == "0"
            ; Verify downloaded file existence and minimum size (VC redist is ~24MB)
            StrCpy $R0 "$VcRedistPath"
            StrCpy $R1 1048576
            Call VerifyDownload

            ; Hash verification
            DetailPrint "$(MSG_03)"
            nsExec::ExecToStack 'powershell -ExecutionPolicy Bypass -Command "Get-FileHash -Path \'$VcRedistPath\' -Algorithm SHA256 | Select-Object -ExpandProperty Hash"'
            Pop $0
            Pop $1
            ${If} $0 != "0"
                MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_04)"
                Delete "$VcRedistPath"
                StrCpy $InstallSuccess "0"
                Goto VCInstallEnd
            ${EndIf}
            ${If} $1 != "${VC_REDIST_HASH}"
                MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_05)"
                Delete "$VcRedistPath"
                StrCpy $InstallSuccess "0"
                Goto VCInstallEnd
            ${EndIf}
            DetailPrint "$(MSG_06)"

            DetailPrint "$(MSG_07)"
            ExecWait '"$VcRedistPath" /install /quiet /norestart' $0
            ${If} $0 == 0
                DetailPrint "$(MSG_08)"
            ${ElseIf} $0 == 3010
                DetailPrint "$(MSG_09)"
                StrCpy $RebootRequired "1"
            ${Else}
                MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_10)"
                StrCpy $InstallSuccess "0"
            ${EndIf}
            ; Clean up downloaded installer
            Delete "$VcRedistPath"
        ${Else}
            MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_11)"
            StrCpy $InstallSuccess "0"
        ${EndIf}
VCInstallEnd:
    ${Else}
        DetailPrint "$(MSG_12)"
    ${EndIf}

    ; ----------------------------------------
    ; WebView2 Runtime Installation
    ; ----------------------------------------
    ${If} $WebView2Installed == "0"
        DetailPrint "$(MSG_13)"
        nsExec::ExecToStack 'powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri \'${WEBVIEW2_URL}\' -OutFile \'$WebView2Path\'"'
        Pop $0
        ${If} $0 == "0"
            ; Verify downloaded file existence and minimum size (WebView2 bootstrapper is ~1.5MB)
            StrCpy $R0 "$WebView2Path"
            StrCpy $R1 524288
            Call VerifyDownload

            ; Hash verification
            DetailPrint "$(MSG_14)"
            nsExec::ExecToStack 'powershell -ExecutionPolicy Bypass -Command "Get-FileHash -Path \'$WebView2Path\' -Algorithm SHA256 | Select-Object -ExpandProperty Hash"'
            Pop $0
            Pop $1
            ${If} $0 != "0"
                MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_15)"
                Delete "$WebView2Path"
                StrCpy $InstallSuccess "0"
                Goto WebView2InstallEnd
            ${EndIf}
            ${If} $1 != "${WEBVIEW2_HASH}"
                MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_16)"
                Delete "$WebView2Path"
                StrCpy $InstallSuccess "0"
                Goto WebView2InstallEnd
            ${EndIf}
            DetailPrint "$(MSG_06)"

            DetailPrint "$(MSG_17)"
            ExecWait '"$WebView2Path" /silent /install' $0
            ${If} $0 == 0
                DetailPrint "$(MSG_18)"
            ${ElseIf} $0 == 3010
                DetailPrint "$(MSG_19)"
                StrCpy $RebootRequired "1"
            ${Else}
                ; インストール失敗後も再チェック（一部環境で失敗コードが出る場合がある）
                Call DetectWebView2
                ${If} $WebView2Installed == "1"
                    DetailPrint "$(MSG_20)"
                ${Else}
                    DetailPrint "$(MSG_21)"
                    MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_22)"
                    StrCpy $InstallSuccess "0"
                ${EndIf}
            ${EndIf}
            ; Clean up downloaded installer
            Delete "$WebView2Path"
        ${Else}
            MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_23)"
            StrCpy $InstallSuccess "0"
        ${EndIf}
WebView2InstallEnd:
    ${Else}
        DetailPrint "$(MSG_24)"
    ${EndIf}

    ; ----------------------------------------
    ; Voice2Doc Application Files
    ; ----------------------------------------
    DetailPrint "$(MSG_26)"

    ; Voice2Doc.exe を個別にコピー
    File "..\dist\Voice2Doc\Voice2Doc.exe"
    ; _internal ディレクトリを丸ごとコピー。上書きインストールで前のバージョンの不要なファイルが残らないよう、
    ; 先に _internal（プログラムのみ。データは無い）を消す
    RMDir /r "$INSTDIR\_internal"
    File /r "..\dist\Voice2Doc\_internal"
    ; アイコンファイルを明示的に配置する（ショートカット・「アプリと機能」の表示アイコンの参照先）。
    ; exe内のアイコン資源だけに頼ると、Windowsのアイコンキャッシュや古いショートカットの再利用で
    ; 旧アイコン/既定アイコンのまま表示されることがあるため、独立した ico を参照させる
    File /oname=$INSTDIR\Voice2Doc.ico "..\assets\icon.ico"
    ; ライセンス表示（MIT の著作権表示と、同梱する第三者ソフトウェアの一覧）を配布物に含める
    File /oname=$INSTDIR\LICENSE.txt "..\LICENSE"
    File /oname=$INSTDIR\THIRD_PARTY_NOTICES.md "..\THIRD_PARTY_NOTICES.md"

    ; Create uninstaller
    WriteUninstaller "$INSTDIR\uninstall.exe"

    ; Create desktop shortcut
    ; 既存のショートカットは一度削除して作り直す（新しいファイルとして扱わせ、アイコンを取得し直させる）
    Delete "$DESKTOP\Voice2Doc.lnk"
    CreateShortCut "$DESKTOP\Voice2Doc.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\Voice2Doc.ico" 0

    ; Create start menu shortcuts
    CreateDirectory "$SMPROGRAMS\Voice2Doc"
    Delete "$SMPROGRAMS\Voice2Doc\Voice2Doc.lnk"
    CreateShortCut "$SMPROGRAMS\Voice2Doc\Voice2Doc.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\Voice2Doc.ico" 0
    CreateShortCut "$SMPROGRAMS\Voice2Doc\Uninstall.lnk" "$INSTDIR\uninstall.exe" "" "$INSTDIR\Voice2Doc.ico" 0

    ; エクスプローラーにアイコンの更新を通知する（SHCNE_ASSOCCHANGED）
    System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'

    ; Registry entries (Uninstall info) - HKCU for user-level install
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" '"$INSTDIR\uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayIcon" '"$INSTDIR\Voice2Doc.ico"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "URLInfoAbout" "${APP_URL}"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoRepair" 1

    ; Save install directory
    WriteRegStr HKCU "Software\${APP_NAME}" "InstallDir" "$INSTDIR"

    ; Conditional completion message based on installation results
    ${If} $InstallSuccess == "1"
        ${If} $RebootRequired == "1"
            MessageBox MB_OK "$(MSG_27)"
        ${Else}
            MessageBox MB_OK "$(MSG_28)"
        ${EndIf}
    ${Else}
        MessageBox MB_OK|MB_ICONEXCLAMATION "$(MSG_29)"
    ${EndIf}
SectionEnd

; ========================================
; Uninstall Section
; ========================================
Section "Uninstall"
    ; Delete all files
    RMDir /r "$INSTDIR"

    ; Delete desktop shortcut
    Delete "$DESKTOP\Voice2Doc.lnk"

    ; Delete start menu
    RMDir /r "$SMPROGRAMS\Voice2Doc"

    ; Delete registry entries (HKCU for user-level install)
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
    DeleteRegKey HKCU "Software\${APP_NAME}"

    ; Uninstall complete message
    MessageBox MB_OK "$(MSG_30)"
SectionEnd

; ========================================
; Event Handlers
; ========================================
Function .onInit
    !insertmacro SelectLanguage

    ; Generate unique temporary file names to avoid conflicts
    GetTempFileName $VcRedistPath
    GetTempFileName $WebView2Path

    ; Detect pre-installed runtimes
    Call DetectVCRuntime
    Call DetectWebView2
FunctionEnd

Function un.onInit
    !insertmacro SelectLanguage
FunctionEnd
