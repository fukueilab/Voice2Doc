# Spec: 話書き / Voice2Doc（音声・動画・テキスト → ローカル文字起こし → ローカル LLM で目的の文章）

作成日: 2026-11-10
作成者: Claude（ユーザーへのインタビューと実装に基づく）

詳細な API・設定・処理の仕様は [spec.md](../../spec.md) にある。この文書は全体の目的と構成をまとめる。

## Background / 背景・課題
会議・面談・思いついたことを録音した音声や動画から、校正した文章・企画書・上司への報告書・お客様との面談記録などを作るには、
文字起こしと整理の手作業がかかる。録音の内容は社外に出したくないことが多いので、クラウドに送らず自分の PC だけで処理したい。

## Goals / ゴール
- 音声・動画ファイルをドロップするだけで、ローカルの Whisper で文字起こしし、ローカル LLM が選んだプロンプトに沿って文章を作る。
- テキストの貼り付けや `.txt` / `.md` のドロップでは文字起こしを省き、同じように文章を作る。
- 初期プロンプトは4つ（文章校正・企画書・報告書・お客様面談記録。英語は Proofreading・Proposal・Report・Customer meeting notes）。
  既定は文章校正。利用者が自分のプロンプト・保存先を追加・編集・バックアップできる。
- 結果を検索できる履歴に残し、テキスト・Word・PDF・Markdown でダウンロードできる。
- 画面・メッセージ・初期プロンプト・インストーラーを日本語と英語で使える（日本語以外の Windows では英語）。
- 管理者権限なしでインストールでき、起動の失敗や白い画面からも自分で立て直す。

## Non-Goals / スコープ外
- クラウドの LLM・音声認識サービスへの対応（文字起こしも生成もローカルのみ）。
- 文字起こしエンジンの同梱（Vibe を利用者がインストールする）。モデルの同梱（初回に自動ダウンロード）。
- 日英以外の言語、macOS / Linux、コード署名。

## Proposed Approach / 実装方針
- **構成**: Python 3.12 + FastAPI/Uvicorn（`src_backends/`）+ PyWebView（`main.py`）+ バニラ HTML/JS/CSS（`src/`）。
  サーバーは `127.0.0.1` だけで待ち受け、アプリのウィンドウからだけ使う。
- **文字起こし**: Vibe の `vibe-server.exe`（whisper.cpp、`large-v3-turbo`）を `vibe_client.py` が起動・管理する。
  見つからないときは openai-whisper を実行中に限り使い、Vibe の導入を促す。動画は Vibe の `ffmpeg.exe` で音声を取り出す。
- **文章生成**: llama-cpp-python で GGUF を直接読み込む。既定は `gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf`（約 2.6GB、Apache 2.0）。
  任意の `.gguf` も指定できる。生成はストリーミングで画面に表示する。
  LLM は Vulkan 版の llama-cpp-python で、GPU（AMD・Intel・NVIDIA）があれば自動で使い、無い・合わないときは CPU で動く
  （設定の「LLM の実行装置」で自動 / GPU / CPU を選べる。詳細は `spec.md`）。
- **初期プロンプト**: `prompt_sets.py` に日英の4つを持つ。表示言語を切り替えると、編集していない初期プロンプトだけを入れ替える。
- **多言語化**: 画面は `src/i18n.js`、API・起動画面・メッセージボックスは `src_backends/i18n.py`。ログとコメントは日本語。
- **配布**: PyInstaller + NSIS のインストーラー（`%LOCALAPPDATA%\Voice2Doc`、ユーザー単位）。VC++ Runtime と WebView2 は無ければ
  SHA-256 を検証してから入れる。タグの push で GitHub Actions がインストーラーを作り、Release の下書きに添付する。

### Affected Files / Interfaces
- 入口: `main.py`（二重起動防止・スプラッシュ・見張り役）、`src_backends/main.py`（API）
- 処理: `whisper_processor.py`、`vibe_client.py`、`local_llm.py`、`transcript_format.py`、`output_formats.py`、`eta.py`
- 設定: `config.py`（`AppConfig`、`APP_VERSION`）、`prompt_sets.py`、`prompt_backup.py`
- 起動: `startup.py`、`single_instance.py`、`setup_checker.py`、`app_logging.py`
- 画面: `src/index.html`・`app.js`・`style.css`、`src/wizard.*`、`src/i18n.js`
- 配布: `voice2doc.spec`、`installer/voice2doc_installer.nsi`、`build*.bat`、`.github/workflows/*.yml`

## Edge Cases / エッジケース
- vibe-server.exe が無い → openai-whisper で続行し、警告を出す。FFmpeg が無い動画 → 案内付きのエラー。
- 同じファイルを何度もドロップ → 1回だけ処理。名前だけ同じで内容が違う → 警告して除く。
- 処理中の中止 → vibe-server を止めて待機を打ち切り、結果ファイルを作らない。
- ポート 8008 が使用中 → 8030 までの空きポート。別プロセスの応答を自分のサーバーと取り違えない。
- 画面が白いまま → 再読み込み → GPU を使わない描画で1回だけ再起動 → 案内。
- 表示言語の切替時に、利用者が初期プロンプトと同じ名前のプロンプトを作っていた → 上書きしない。

## Testing Plan / テスト計画
- `python -m pytest src_backends/ -v`（外部依存はモック）。主なもの: API（`test_api_endpoints.py`）、初期プロンプトと日英
  （`test_i18n.py`）、vibe-server（`test_vibe_client.py`）、起動（`test_startup.py`・`test_single_instance.py`）、出力形式
  （`test_output_formats.py`）、インストーラー（`test_installer_script.py`）、公開文書（`test_docs_consistency.py`）、版（`test_version.py`）。
- `python scripts/ruff_baseline.py`、`node --check` で全 JS。

## End-to-End Verification / 完了確認手順
1. `build_installer.bat` で `dist\Voice2Doc-Setup.exe` を作り、日本語と英語の Windows（または表示言語）でインストールする。
2. 起動して初回ウィザードを完了し、処理メニューに4つの初期プロンプトが並び、文章校正が選ばれていることを確かめる。
3. 短い音声ファイルを文章校正で処理し、履歴から txt / docx / pdf / md をダウンロードする。
4. 環境設定で English に切り替え、画面と初期プロンプトが英語になることを確かめる。

## Open Questions / 未決事項
- 安定版 1.0.0 の公開時期（beta での実機テストの結果による）。
- アンインストール時に設定・履歴を残す選択肢を設けるか（現在は既知の制限）。
