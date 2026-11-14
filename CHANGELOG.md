# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-releases (`-beta.N`, `-rc.N`) are test builds before the stable version. See [docs/RELEASING.md](docs/RELEASING.md).

## [Unreleased]

## [1.0.0-beta.3] - 2026-11-13

First public pre-release, for testing before the stable 1.0.0 release.

### Features
- **Pipeline**: audio / video → audio extraction (FFmpeg) → local transcription (Whisper via Vibe's `vibe-server.exe`,
  whisper.cpp with Vulkan GPU; the Python `openai-whisper` is used as a fallback while vibe-server.exe is not found) →
  punctuation and line breaks for the raw transcript → text generation with a local LLM (llama-cpp-python) → Markdown.
- **Local LLM on the GPU or the CPU**: the default model is Gemma 4 E2B (`gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf`, about 2.6 GB,
  Apache-2.0); any `.gguf` can be chosen as the AI model. The app ships the Vulkan build of `llama-cpp-python` 0.3.36, so the
  model runs on an AMD, Intel or NVIDIA GPU when one is usable (*LLM device*: Auto / GPU / CPU in Settings > Model; the device
  in use is shown there). The Vulkan loader (`vulkan-1.dll`) comes with the GPU driver and is not redistributed; PCs without
  it use a bundled CPU build of the same libraries. If loading or generating on the GPU fails, or the app closed unexpectedly
  while using it, *Auto* falls back to the CPU.
- **Text input**: paste or type text, or drop `.txt` / `.md` files, to skip transcription. Typed text is saved as a `.txt`
  next to the result.
- **Prompt cards** with four built-in prompts — Proofreading (the default), Proposal, Report and Customer meeting notes
  (文章校正・企画書・報告書・お客様面談記録 in Japanese) — plus your own prompts with output folders, an option to append
  the transcript, descriptions, "Don't use this prompt", and backup / restore of all saved prompts (JSON in Downloads).
  Files can be dropped straight onto a card.
- **History** with full-text search, preview, copy, bulk "remove from history" / "delete files", and download as `.txt`
  (UTF-8 BOM, CRLF), `.docx`, `.pdf` (Japanese font embedded, no Word needed) or `.md`.
- Results are named `<source file name>-<H1 title>`; a file that was already processed is skipped (recorded in `done.log`;
  the source file is not moved).
- **Progress dialog** with step names, an estimated time for speech recognition that learns from past runs, elapsed time and
  live streaming of the generated text during AI generation, and a working *Cancel* button.
- **Transcription models**: the model is chosen in Settings or the first-run wizard. A model that is not on the PC is
  downloaded only after a confirmation dialog; the default model (`ggml-large-v3-turbo.bin`, about 1.6 GB) is downloaded by
  Vibe itself when Vibe is installed. Changing the model restarts vibe-server with it without restarting the app.
- English / Japanese user interface. The first-run wizard picks the language from Windows; unedited built-in prompts and
  the speech recognition language follow the language setting.
- **Settings**: model tab (transcription model, path to `vibe-server.exe`, AI model, LLM device), preferences (language,
  light / dark, default download format), and an *About* tab with version, license, links and the required software.
- **First-run wizard** that checks the prerequisites (VC++ Runtime, WebView2, FFmpeg, vibe-server.exe), detects Vibe after you
  install it without restarting the app (`POST /vibe/activate`), and lets you point to `vibe-server.exe` yourself.
- **Robust startup**: single instance (a second launch brings the window to the front), splash screen with the cause and a
  *Try again* button on failure, automatic port selection (8008–8030), detection of a blank window with reload and a
  one-time restart in safe rendering mode (GPU off).
- Activity log per launch in the `logs` folder (latest 30 kept; prompt and transcript text are never logged).
- **Windows installer** (per-user, no administrator rights) built with PyInstaller and NSIS. Its language follows Windows
  (Japanese on a Japanese Windows, English otherwise); the last page explains that Vibe is required. VC++ Runtime and
  WebView2 are downloaded with SHA-256 verification when missing.
- GitHub Actions: CI (ruff baseline and pytest) and a release workflow that builds the installer for a `v*` tag and
  attaches it to a draft release.

### Notes
- Vibe (https://github.com/thewh1teagle/vibe/releases) must be installed separately; it provides `vibe-server.exe` and
  `ffmpeg.exe`. The Whisper model (about 1.6 GB) is downloaded by Vibe, and the local LLM (about 2.6 GB) on first use.
- Uninstalling removes `%LOCALAPPDATA%\Voice2Doc`, including settings, history and cached models (known limitation).
- The installer is not code-signed, so Windows SmartScreen may warn, and Windows 11 Smart App Control blocks it when
  turned on (see the installation guide).

[Unreleased]: https://github.com/fukueilab/Voice2Doc/compare/v1.0.0-beta.3...HEAD
[1.0.0-beta.3]: https://github.com/fukueilab/Voice2Doc/releases/tag/v1.0.0-beta.3
