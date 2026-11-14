# Releasing Voice2Doc

How a release is made. CI (`.github/workflows/ci.yml`) runs `ruff` and `pytest` on every push and pull request.
Pushing a `v*` tag runs `.github/workflows/release.yml`, which builds the installer on a Windows runner and attaches it to a
**draft** GitHub Release. Pushing, tagging and publishing a release are always done deliberately by the maintainer.

## Versioning

[Semantic Versioning 2.0.0](https://semver.org/) with pre-release stages. The single source of truth is `APP_VERSION` in
`src_backends/config.py`; `installer/voice2doc_installer.nsi` (`APP_VERSION`) must carry the same string
(`src_backends/test_version.py` checks the format, the match and the `CHANGELOG.md` heading).

```
1.0.0-beta.3 → 1.0.0-beta.4 → … → 1.0.0-rc.1 → 1.0.0 → 1.0.1 / 1.1.0-beta.1 …
```

| Stage | Example | Meaning | GitHub Releases |
| --- | --- | --- | --- |
| Test build | `1.0.0-beta.3`, `1.0.0-beta.4` | Features are complete, being tested and fixed. Bump N for each fix | Pre-release |
| Release candidate | `1.0.0-rc.1` | Only blocker fixes. Promote from `beta` when there are no known open bugs and the end-to-end steps pass | Pre-release |
| Stable | `1.0.0` | Stable release | Latest |
| Fix / feature after stable | `1.0.1`, `1.1.0-beta.1` → `1.1.0` | Bug fix → PATCH, new feature → MINOR (tested as beta first) | as above |
| Breaking change | `2.0.0-beta.1` → `2.0.0` | e.g. an incompatible settings format | as above |

- Order: `1.0.0-beta.3 < 1.0.0-beta.4 < 1.0.0-rc.1 < 1.0.0`.
- A pushed tag is never moved; fix forward with the next version.
- Stable (no suffix) and any MAJOR change need an explicit decision by the maintainer.

## Steps for a new version

1. Finish the work on a `feature/<slug>` or `fix/<slug>` branch; update `spec.md`, the README(s) if user-visible, and
   `CHANGELOG.md` (`[Unreleased]` → `## [x.y.z-beta.N] - YYYY-MM-DD`, plus the link at the bottom).
2. Bump `APP_VERSION` (both places above), the READMEs' badge and
   `docs/install-guide*.md`.
3. Run the checks:
   ```
   python -m pytest src_backends/ -v
   python scripts/ruff_baseline.py
   node --check src/app.js && node --check src/wizard.js && node --check src/i18n.js
   ```
4. Build locally if you want to test the installer (needs Python 3.12, PyInstaller and
   [NSIS 3](https://nsis.sourceforge.io/) at `C:\Program Files (x86)\NSIS\`): `build_installer.bat` →
   `dist\Voice2Doc-Setup.exe`. Smoke-test it on a clean user (install, start, switch the language, process a short file).
5. Merge through a pull request into `main` (no direct pushes to `main`).
6. Tag on `main` and push the tag (maintainer only):
   ```
   git tag v1.0.0-beta.4
   git push origin v1.0.0-beta.4
   ```
   The Release workflow checks that the tag matches `APP_VERSION`, runs the tests, builds `Voice2Doc-Setup.exe` and creates a
   **draft** release with the installer and its SHA-256. A tag with a suffix (`-beta.N`, `-rc.N`) becomes a pre-release.
   The workflow installs the Vulkan build of `llama-cpp-python` (prebuilt wheel, so the LLM runs on the GPU when one is
   usable and on the CPU otherwise). That build needs the Vulkan loader `vulkan-1.dll`, which the GPU driver installs and which
   we do **not** redistribute. For PCs without it, `scripts/fetch_cpu_llama_lib.py` (run by `build.bat` and the workflow before
   PyInstaller) fetches the CPU build of the same version, and `voice2doc.spec` bundles it as `llama_cpp/lib_cpu`; the app
   switches to it at start-up when no loader is found. Keep the version in that script equal to `requirements.txt`.
7. Open the draft on GitHub, paste the `CHANGELOG.md` entry, mention the SmartScreen warning below, and publish it.

## First public release (1.0.0-beta.3)

The public repository starts with a single commit and no earlier history. The commit holds exactly the files that
`git ls-files` lists on the release branch (the working branch's older commits carry personal author data, so they are never
published). Nothing is pushed until the maintainer does the steps below.

### Files in the public commit

`.gitignore` and `git ls-files` decide the file set; never use `git add -f`.

| Included | Not included (kept out by `.gitignore` or by not being tracked) |
| --- | --- |
| Source: `main.py`, `src/`, `src_backends/` (including tests), `scripts/`, `installer/`, `voice2doc.spec`, `build*.bat`, `requirements.txt`, `ruff*`, `pytest.ini` | `dist/`, `build/` (installer, PyInstaller output, the fetched CPU llama.cpp libraries) |
| Documents: `README*.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `spec.md`, `docs/` (guides, `docs/spec/voice2doc.md`, `SPEC_TEMPLATE.md`, `RELEASING.md`, screenshots) | Models and recordings: `*.gguf`, `*.bin`, `*.pt`, loose `*.wav` / `*.mp3` / `*.mp4` / `*.m4a` / `*.mov` / `*.mkv` at the root |
| `assets/` (icon), `fonts/` (IPAex Gothic with its license files, 6 MB — the only file over 2 MB) | `config.json`, `default.json`, `history.json`, `input/`, `output/`, `done/`, logs |
| `.github/` (CI, release workflow, issue / PR templates), `.gitattributes`, `.gitignore` | `.claude/worktrees/`, `.claude/settings.local.json`, `issue.md`, editor folders, caches (`__pycache__`, `.ruff_cache`, `.pytest_cache`) |
| *(notes that only the maintainer needs stay in the working branch and are removed from the public commit, step 3)* | The Vibe installer, `vibe-server.exe`, FFmpeg, the Vulkan loader — never redistributed |

`src_backends/test_docs_consistency.py` enforces most of this: no old names, no personal data patterns, no model / recording /
build files, no file over 2 MB except the font, and only the one `docs/spec` file.

### Steps

1. On the release branch with a clean working tree, run the checks (see "Steps for a new version", item 3) and confirm the
   version is `1.0.0-beta.3` everywhere (`python -m pytest src_backends/test_version.py`).
2. Review what will be published:
   ```
   git status --short                                  # empty
   git ls-files | wc -l                                # about 100 files
   git ls-files -z | xargs -0 du -ch | sort -h | tail  # largest: fonts/ipaexg.ttf; nothing else over 2 MB
   git ls-files | grep -Ei '\.(gguf|bin|pt|wav|mp3|mp4|m4a|exe)$|^(dist|build)/|config\.json|history\.json'   # no output
   ```
3. Build the one-commit history on an orphan branch (the author is set only for this command, not in the repository config).
   Keep the old local branch under another name; do not delete it until the new one is verified:
   ```
   git branch -m public/main public/main-old           # only if public/main already exists
   git switch --orphan public/main
   git add -A                                          # respects .gitignore
   git rm -r -q --cached <maintainer-only files>       # maintainer notes stay in the working branch only (the list is in the maintainer's notes)
   git ls-files | wc -l                                # step 2's count minus those files; nothing else may differ
   git -c user.name="Fukuei Lab" -c user.email="345715598+fukueilab@users.noreply.github.com" commit -m "Voice2Doc v1.0.0-beta.3 - initial public release"
   git log --oneline                                   # exactly one commit
   git log --format='%an <%ae> | %cn <%ce>' | sort -u  # only the no-reply address
   ```
   (`git switch --orphan` starts with an empty index and leaves the working tree as it is, so `git add -A` re-adds it;
   `git ls-files` must equal the list from step 2. Untracked files that are not ignored would be added too, so step 2 starts
   with `git status --short` being empty.)
4. Run the tests on that branch (`python -m pytest src_backends/ -q`).
5. Create the empty public repository `fukueilab/Voice2Doc` on GitHub (no README, license or .gitignore, so that the first
   push is not rejected), and turn on *Private vulnerability reporting* (Settings → Security) for [SECURITY.md](../SECURITY.md).
6. Push only that branch as `main` (maintainer only):
   ```
   git remote add public https://github.com/fukueilab/Voice2Doc.git
   git push public public/main:main
   ```
7. Tag and push the tag to start the Release workflow:
   ```
   git tag v1.0.0-beta.3 public/main
   git push public v1.0.0-beta.3
   ```
8. Publish the draft release created by the workflow (step 7 of "Steps for a new version").
9. Keep developing on the working branch (it keeps the maintainer-only files that the public commit omits). For later releases, make the
   tree the same way (steps 2–3, including the `git rm --cached` line) and commit it **on top of** the published `main` instead of creating an orphan branch.

## Things to tell users

- The installer is **not code-signed**, so Windows SmartScreen may warn ("More info → Run anyway"), and on PCs with
  **Smart App Control** turned on it is blocked outright (the user has to turn Smart App Control off; see the install guide).
  Code signing (for example Azure Trusted Signing) of the installer, `Voice2Doc.exe` and the files in `_internal` would
  remove this; it is not set up yet.
- **Vibe is required** (https://github.com/thewh1teagle/vibe/releases): it provides `vibe-server.exe` and `ffmpeg.exe`,
  which the installer does not include.
- The Whisper model (about 1.6 GB) and the local LLM (about 2.6 GB) are downloaded on first use and are not part of the installer.
- Uninstalling removes the install folder, which also holds settings, history and cached models (known limitation).

## Before every public release

- Check `git log --format='%an <%ae>' | sort -u` shows only a no-reply address.
- Make sure no models, recordings, `config.json` / `history.json` or `.env` are tracked (`git ls-files`), and that the
  screenshots in `docs/images/` contain no personal data.
- Review `THIRD_PARTY_NOTICES.md` when dependencies change.
