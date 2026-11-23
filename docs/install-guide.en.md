# Voice2Doc Installation Guide

Applies to version: 1.0.0-beta.4 · Japanese version: [install-guide.md](install-guide.md)
Distributed as: `Voice2Doc-Setup.exe` (NSIS installer, about 300 MB; under **Assets** of the newest version on the [Releases page](https://github.com/fukueilab/Voice2Doc/releases))

## 1. Requirements

- OS: Windows 10 / 11 (64-bit)
- Administrator rights: **not needed** (per-user install into `%LOCALAPPDATA%\Voice2Doc`)
- **Vibe** (https://github.com/thewh1teagle/vibe/releases): **install it yourself — it is not included.** Vibe provides
  `vibe-server.exe` (speech recognition) and `ffmpeg.exe` (audio extraction from videos). The app finds them in
  `%LOCALAPPDATA%\vibe\`, `%LOCALAPPDATA%\Programs\vibe\`, `Program Files\vibe`, Vibe's uninstall entry (per-user or all users) or its Start menu shortcut; another location can be set in Settings.
- Internet connection:
  - During installation: needed to download the Visual C++ Runtime / WebView2 Runtime if they are missing
  - On first start: needed for the speech recognition model (about 1.6 GB, downloaded by Vibe) and the local LLM
    (about 2.6 GB, downloaded by the app). See "4. First start".
- Disk space: about 0.9 GB for the app, plus the models — plan for **7 GB or more** (Vibe itself not included)

## 2. Installation

1. Install Vibe (https://github.com/thewh1teagle/vibe/releases) if you have not yet.
2. Copy `Voice2Doc-Setup.exe` to any folder on the PC (for example the desktop).
3. Double-click it. No administrator rights are needed. The installer language follows your Windows language
   (Japanese or English).
4. Follow the wizard:
   1. Welcome → **Next**
   2. License (MIT) → agree
   3. Install folder (default `%LOCALAPPDATA%\Voice2Doc`; keep the default unless you have a reason to change it)
   4. Installation
      - If the Visual C++ Redistributable or the WebView2 Runtime is missing, it is downloaded from Microsoft, verified with
        SHA-256 and installed silently. It is skipped when already installed.
      - `Voice2Doc.exe` and its files are copied to the install folder, and desktop / Start menu shortcuts are created.
   5. A completion message appears. If it says a restart may be required (after installing VC++ / WebView2), restart the PC
      once before starting the app. If it says some components failed, see "5. Troubleshooting".
   6. The last page says that Voice2Doc uses Vibe for speech recognition and that, if Vibe is not installed yet, you install it
      when the app first starts. **Launch Voice2Doc** is checked; press **Finish** to start the app. At that first start the
      app guides you: download the Windows installer
      (for example `vibe_3.3.2_x64-setup.exe`, the name for Windows on Intel/AMD PCs as of 13 Nov 2026; the version number changes in newer releases) from **Assets** on the page that opens and run it; Vibe then downloads a speech recognition
      model, and you can close its window with the × afterwards.
5. Start **Voice2Doc** from the desktop or the Start menu. The first-run wizard opens; its language follows your Windows
   language and can be changed at the top of the first page. If its prerequisite check does not find Vibe
   (vibe-server.exe), it shows the same guidance with a link; install Vibe and press **Check again** (no app restart needed), or use **Specify the location of vibe-server.exe** if it is in
   an unusual place. **Next** stays disabled until all four prerequisites (VC++, WebView2, FFmpeg, vibe-server) show as ready
   (advanced users can use **Continue without checking**).
   You can change the language later in *Settings → Preferences → Language*.

## 3. Uninstallation

1. Start menu → **Voice2Doc → Uninstall**, or *Settings → Apps → Voice2Doc → Uninstall*.
2. Confirm.

**⚠️ Known limitation:** uninstalling deletes the whole install folder (`%LOCALAPPDATA%\Voice2Doc`). Your settings
(`config.json`), history and cached models are stored in the same folder, so they are lost too. Back the folder up first if you
want to keep them. Vibe is a separate app and is not removed.

## 3.5 Shortcut icons

The installer places `Voice2Doc.ico` in the install folder and recreates the desktop and Start menu shortcuts pointing to it.
If an old icon is still shown, Windows' icon cache is stale: restart Explorer, run `ie4uinit.exe -show`, or sign out / restart.

## 4. First start

- The default speech recognition model (`ggml-large-v3-turbo.bin`, about 1.6 GB) is downloaded by Vibe itself when you install Vibe.
  The app never downloads a transcription model automatically when you run a recognition.
- When you choose another model under Settings > Model > *Transcription model* and press **Save**, and that model is not on this PC,
  a dialog says the model will be downloaded and that it takes time. **Download** starts the download (a model that is already
  downloaded is used until it finishes); **Cancel** does not save. The app downloads the local LLM on first use.
- The installer does not contain these model files. An internet connection is required and it can take several minutes.
- You can use other parts of the app meanwhile, but transcription cannot start until the download is finished.
- The local LLM (Gemma 4 E2B, about 2.6 GB) runs on the GPU automatically when one is available (AMD, Intel or NVIDIA with Vulkan
  support) and on the CPU otherwise. Keep the graphics driver up to date. Settings > Model > *LLM device* lets you choose
  Auto / GPU / CPU and shows the device actually in use. If the app closes unexpectedly while using the GPU, the CPU is used
  automatically from the next start, and a notice is shown once.

## 5. Troubleshooting

| Symptom | What to do |
|---|---|
| Microsoft Edge says the file "isn't commonly downloaded" and does not save it | The installer is new and not code-signed, so Edge's reputation check holds it back (it is not a virus detection). In the download panel click **… (More actions)** next to the file, choose **Keep** (保存), and confirm if asked (**Keep anyway**; the wording varies with the Edge version). Firefox and Chrome download it directly |
| "Windows protected your PC" (SmartScreen) | The installer is not code-signed. If you downloaded it from this repository's [Releases page](https://github.com/fukueilab/Voice2Doc/releases), click **More info → Run anyway** |
| "Smart App Control blocked an app", even when run as administrator | Smart App Control (Windows 11) blocks unsigned apps and offers no way to allow one. To install, turn it **off** in *Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings*, then run the installer again. **Note: on most Windows versions it cannot be turned back on without resetting or reinstalling Windows.** |
| "Some components failed" at the end of the installation | The automatic installation of the Visual C++ Redistributable or the WebView2 Runtime failed. Install them manually:<br>• VC++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe<br>• WebView2 Runtime: https://developer.microsoft.com/en-us/microsoft-edge/webview2/ |
| The window stays blank | The app notices a blank window after about 10 s, reloads it and then restarts once with GPU acceleration turned off ("safe rendering mode"; Settings > Preferences shows a button to go back). If that fails too, a message explains what to try. The WebView2 Runtime may also be missing: install it manually, restart the PC and start the app again. When reporting, attach the log from the `logs` folder |
| A "failed to start" error dialog appears right after launch | Read the error in the dialog. A missing VC++ Runtime is the most common cause |
| Speech recognition stays at "downloading the model" | Check your internet connection. Depending on your line it can take several minutes up to about 10 minutes |
| "vibe-server.exe was not found" or a banner says the Python speech recognition is used | Install Vibe (`vibe_3.3.2_x64-setup.exe` under **Assets** at https://github.com/thewh1teagle/vibe/releases), then press **Check again** in the warning — no restart needed. If you installed it somewhere else, set the path to `vibe-server.exe` in *Settings → Model* |
| Video files fail with "FFmpeg was not found" | Check that `ffmpeg.exe` is in the Vibe folder (next to `vibe-server.exe`). If not, reinstall Vibe or put `ffmpeg` on your `PATH` |

## 6. Notes

- The installer is built with NSIS; the source is [installer/voice2doc_installer.nsi](../installer/voice2doc_installer.nsi).
- For building the installer see the [Build section of the README](../README.md#build) (`build_installer.bat`).
