# Security Policy

## Supported versions

Security fixes are provided for the latest release only. Voice2Doc is currently a pre-release (`1.0.0-beta.x`).

## Reporting a vulnerability

Please **do not** open a public issue for security problems.
Report them privately through GitHub's
[private vulnerability reporting](https://github.com/fukueilab/Voice2Doc/security/advisories/new).

We aim to acknowledge reports within 7 days. Please include the version, steps to reproduce and the impact.

## Scope notes

- Voice2Doc runs a local web server bound to `127.0.0.1` only (port 8008, or the next free port up to 8030 if that one is taken) that is used only by its own window. Requests sent by other web pages (other than plain page navigation) or with another host name are rejected.
- Audio, video, transcripts and generated text are processed entirely on your PC and are never sent to an external service
  (see the Privacy section of the [README](README.md)). The app goes online only to download the Whisper and LLM model files
  from Hugging Face (or, for the `openai-whisper` fallback, from OpenAI's download server).
- The installer downloads the Visual C++ Redistributable and WebView2 Runtime from Microsoft and verifies their SHA-256 hashes.
- Links in Settings → About and the first-run wizard open in your default browser; the local server opens only `https`
  links to a fixed set of sites (GitHub, Fukuei Lab, YouTube).
- Vibe (vibe-server.exe and FFmpeg, installed by the user), Whisper models, Gemma models and other
  [third-party software](THIRD_PARTY_NOTICES.md) belong to their own projects; please report issues in them to those projects.
