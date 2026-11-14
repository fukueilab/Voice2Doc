# Contributing to Voice2Doc

Voice2Doc is a personal project. Issues and suggestions can be posted in English or Japanese, but replies may take a long
time, and we may not be able to answer every one. Pull requests are not refused either, but depending on the direction of the
project and what we can maintain, we may not be able to merge them. For larger changes, please open an issue to discuss the
idea first.
（話書き(Voice2Doc)は個人開発のプロジェクトです。Issue や提案は日本語でも英語でも投稿できますが、お返事に時間がかかったり、
すべてにはお答えできなかったりします。プルリクエストも拒否はしませんが、プロジェクトの方針や保守の都合により、取り込めない場合が
あります。大きな変更は、事前に Issue でご相談ください。）

## Reporting bugs / requesting features

- Search [existing issues](https://github.com/fukueilab/Voice2Doc/issues) first.
- Use the issue templates. For bugs, include your Windows version, the app version (shown at the top right of the window),
  the transcription engine and AI model you use (Settings → Model), the type of file you processed, and the error message shown in the app.
- Please remove personal information (names, paths, transcripts) from logs and screenshots.
- Security problems: do **not** open a public issue. See [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/fukueilab/Voice2Doc.git
cd Voice2Doc
pip install -r requirements.txt
python main.py
```

- Python 3.12, Windows 10/11, and [Vibe](https://github.com/thewh1teagle/vibe/releases) installed — it provides
  `vibe-server.exe` and `ffmpeg.exe`, which are not in the repository (see the [README](README.md#run-from-source)).
- Backend: `src_backends/` (FastAPI). Frontend: `src/` (plain HTML/JS/CSS). Desktop shell: `main.py` (pywebview).
- The architecture, API and settings are documented in [spec.md](spec.md) (Japanese), with an overview in
  [docs/spec/voice2doc.md](docs/spec/voice2doc.md). Larger features get their own spec in `docs/spec/<feature>.md` (template: [docs/SPEC_TEMPLATE.md](docs/SPEC_TEMPLATE.md)) — please discuss the design in an issue first.
- Comments, log messages and error messages in the code are written in Japanese; follow the surrounding code.

## Before opening a pull request

```bash
python -m pytest src_backends/ -v
python scripts/ruff_baseline.py
```

- **Tests**: add or update tests for your change. Use the fixtures in `src_backends/conftest.py` (`isolate_config`,
  `test_client`, …) and fakes for vibe-server.exe, Whisper and the LLMs. Tests must never touch your real settings, history, output folders or
  `~/Downloads` (patch `Path.home()` — see `fake_home` in `test_api_endpoints.py`) and must never download models.
- **Lint**: the code base still has older `ruff` findings that are recorded in `ruff-baseline.json`.
  `python scripts/ruff_baseline.py` fails only if your change **adds** findings (new files start from zero). If you fix existing ones,
  run `python scripts/ruff_baseline.py --update` to lower the baseline.
- **Never commit** model files (`*.bin`, `*.gguf`), audio / video recordings, `config.json`, `history.json` or `.env`.
- Update `spec.md` when behavior or APIs change, and add an entry under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md).
  Update `README.md` / `README.ja.md` if user-visible behavior changes.
- Use a branch named `feature/<slug>` or `fix/<slug>`, keep one task per commit, and explain *why* in the commit message.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
