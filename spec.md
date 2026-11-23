# 話書き (Voice2Doc) - 仕様書

対象バージョン: 1.0.0-beta.6。この文書は現在の動作だけを記載する（変更の記録は [CHANGELOG.md](CHANGELOG.md)）。
全体の概要は [docs/spec/voice2doc.md](docs/spec/voice2doc.md) を参照。

## 1. プロジェクト概要

音声・動画ファイルを Whisper でローカルに文字起こしし、ローカル LLM（Gemma 4）が、編集できるプロンプトに沿って目的の文章
（文章校正・企画書・報告書・お客様面談記録など）を生成する Windows 向けデスクトップアプリ。テキストの貼り付け・`.txt` / `.md` の
ドロップでは文字起こしを省く。結果は検索できる履歴に残り、テキスト・Word・PDF・Markdown でダウンロードできる。
画面は日本語と英語に対応する。

## 2. アーキテクチャ

| レイヤー | 技術 | 役割 |
|---------|------|------|
| デスクトップUI | PyWebView 6.2.1（WebView2） | ネイティブウィンドウ表示 |
| バックエンド | FastAPI + Uvicorn | REST API + 静的ファイル配信（`127.0.0.1` のみ） |
| 音声認識 | [Vibe](https://github.com/thewh1teagle/vibe/releases) の `vibe-server.exe`（whisper.cpp、Vulkan GPU）。利用者がインストールし、アプリには同梱しない | 音声→テキスト。UI からは選べず固定 |
| 音声認識（予備） | openai-whisper (20250625) | `vibe-server.exe` が見つからないときだけ、実行中に限り自動で使う |
| 動画処理 | FFmpeg（Vibe の `ffmpeg.exe`、無ければ PATH の `ffmpeg`。同梱しない） | 動画→音声抽出・音声長の取得 |
| 文章生成 | llama-cpp-python（Vulkan 版 0.3.36）+ Gemma 4 E2B QAT（GGUF） | ローカル LLM。GPU（AMD・Intel・NVIDIA、Vulkan 対応）があれば自動で使い、無い・合わないときは CPU で動く。設定で任意の `.gguf` や実行装置も指定できる |
| 設定 | JSON | `config.json` |

## 3. ディレクトリ構成

```
Voice2Doc/
├── main.py                    # エントリポイント（二重起動防止・スプラッシュ・サーバー起動・見張り役）
├── requirements.txt           # 依存パッケージの唯一の定義
├── voice2doc.spec             # PyInstaller ビルド設定
├── build.bat / build_installer.bat  # ビルド（PyInstaller / + NSIS）。ASCII のみ
├── installer/voice2doc_installer.nsi  # NSIS インストーラー（日英、完了ページで Vibe を案内）
├── assets/icon.ico            # アプリアイコン（scripts/make_icon.py で生成）
├── fonts/                     # PDF 出力用の IPAex ゴシックとライセンス
├── scripts/                   # make_icon.py、ruff_baseline.py（ruff の既存指摘の増加だけを検出）
├── .github/                   # CI（ci.yml）・リリース（release.yml）・Issue/PR テンプレート
├── docs/                      # インストール手順（日英）・リリース手順・仕様（spec/）・画面画像
├── src/                       # フロントエンド（index.html, app.js, style.css, i18n.js, wizard.*）
└── src_backends/              # バックエンド
    ├── main.py                # FastAPI アプリ（API・処理フロー）
    ├── config.py              # 設定（AppConfig、APP_VERSION、読み書き・キャッシュ・リセット）
    ├── prompt_sets.py         # 言語別の初期プロンプトと、言語切替時の入れ替え
    ├── prompt_backup.py       # 保存済みプロンプトのバックアップ/復元
    ├── i18n.py                # API・起動画面のメッセージの日英辞書
    ├── vibe_client.py         # vibe-server.exe の検出・起動・停止・文字起こし・モデル取得、FFmpeg の検出
    ├── whisper_processor.py   # 文字起こしの入口（vibe-server / openai-whisper）、動画の音声抽出・音声長
    ├── local_llm.py           # llama-cpp-python（モデル取得・ストリーミング生成）
    ├── transcript_format.py   # 文字起こしの句読点・改行の補完（日本語/英語の規則）
    ├── output_formats.py      # Markdown → txt / docx / PDF 変換
    ├── eta.py                 # 音声認識の推定時間（学習係数）・経過時間
    ├── local_guard.py         # ローカル API の保護（Host・Origin の検査）
    ├── startup.py             # 起動待機・ポート選択・WebView2 データフォルダ・見張り役・安全モード・スプラッシュ
    ├── single_instance.py     # 二重起動の防止（名前付きミューテックス）と既存ウィンドウの前面化
    ├── setup_checker.py       # 前提条件（VC++ / WebView2 / FFmpeg / vibe-server / ディスク / GPU）
    ├── app_logging.py         # 起動ごとのアクティビティログ・未処理例外の記録
    ├── path_utils.py          # パス解決（PyInstaller / 開発環境）
    ├── conftest.py            # pytest 共通フィクスチャ
    └── test_*.py              # テスト
```

## 4. データディレクトリ

| パス | 用途 |
|------|------|
| `%LOCALAPPDATA%\Voice2Doc\`（インストール版）/ `~\Documents\Voice2Doc\`（ソース実行） | データフォルダ（以下 `BASE_DIR`） |
| `BASE_DIR\input\` | アップロードしたファイル（処理後も移動・削除しない） |
| `BASE_DIR\output\` | 生成した Markdown と文字起こし `.txt`（プロンプトごとに別の保存先も指定できる） |
| `BASE_DIR\done.log` | 処理済みファイルの記録（タイムスタンプ + ファイル名のタブ区切り、1行1件） |
| `BASE_DIR\config.json` / `default.json` | 設定 / 「初期値に戻す」の基準 |
| `BASE_DIR\history.json` | 履歴の非表示リスト（ファイル ID + 非表示にした時刻） |
| `BASE_DIR\logs\` | アクティビティログ（起動ごとに `YYYY-MM-DDTHH-MM-SS.log`、最新30件） |
| `BASE_DIR\.cache\llm\` | ローカル LLM のモデル |
| `BASE_DIR\.cache\whisper\` | openai-whisper のモデル |
| `BASE_DIR\.cache\vibe\` | 文字起こしモデルの保存先の1つ（Vibe の `%LOCALAPPDATA%\github.com.thewh1teagle.vibe\` も探す） |
| `BASE_DIR\.cache\eta_stats.json` / `gpu_info_cache.json` | 推定時間の学習係数 / GPU 検出結果のキャッシュ |
| `BASE_DIR\safe_render` | 描画の安全モード（GPU 無効）の目印 |
| `~\.voice2doc\run-<pid>\` | WebView2 のデータフォルダ（起動ごと。動いていないプロセスの分は起動時に削除） |

## 5. 起動フロー

1. **二重起動の防止**: `main.py` は最初に名前付きミューテックス `Local\Voice2Doc.SingleInstance` を取得する。既に起動中なら、
   起動済みのウィンドウ（題名「話書き - Voice2Doc」、またはウィザードの「話書き - 初回設定ウィザード」/「Voice2Doc - Setup wizard」）を
   最小化解除して前面に出し、何も表示せず終了する（2つ目はログも作らない）。
2. ウィンドウを先に作り、スプラッシュ「起動しています…」（OS の言語。設定の読み込み後は表示言語）を表示する。
   以下は `webview.start(boot)` で GUI が動いた後に別スレッドで行う。
3. `setup_checker.ensure_all_prerequisites()` で前提条件を確認する（失敗しても警告のみで続行）。GPU 検出の結果はキャッシュする。
4. FastAPI サーバーを別スレッドで起動する（ポートは 8008、使用中なら 8030 までの空き。CORS と API の保護も同じポートに合わせる）。
   **API の保護**（`local_guard.py`、全ての要求が対象）: `Host` が `127.0.0.1:<port>` / `localhost:<port>` 以外は 400。`Origin` があれば
   自分の画面のもの以外は 403。`Origin` が無く `Sec-Fetch-Site` が他サイトなら、ページの移動（GET）以外は 403。静的ファイルは、`..`・`\`・
   `:`・先頭の `/` を含むパスと、`src` フォルダの外を 403 にする。FastAPI の自動生成の API 文書（`/docs` 等）は無効。
5. `startup.wait_for_server()` が、サーバーのスレッドが生きていて `/health` の `instance_id` が自分のものと一致するまで待つ。
   失敗時はスプラッシュに原因・ログの場所・「もう一度試す」（アプリの再起動）を表示する。
6. `wizard_completed` が偽なら `/wizard.html`（題名も表示言語のウィザード名へ）、真なら本画面を `load_url` で開く。
7. **白い画面の検知と復旧**: 本画面は `/health` が初めて成功した時点で `POST /ui-ready {instance_id}` を送る。見張り役
   （`startup.run_watchdog`）は10秒待ち、来なければ ①診断をログ → ②再読み込み（8秒）→ ③`--disable-gpu` を付けて1回だけ自己再起動
   （安全モード）→ ④メッセージボックスで案内、の順で立て直す。安全モードで表示できたら `safe_render` を作り、以後は最初から安全モード
   （環境設定に注記と「通常の描画に戻す」ボタン）。`index.html` / `wizard.html` 先頭のインラインスクリプトは、i18n.js が読めない段階の
   スクリプトエラー・読み込み失敗で「画面を表示できませんでした」と再読み込みボタンを出す（文言は日英を HTML に直接持ち、
   アプリで選んだ表示言語（localStorage）を優先、無ければ OS の言語）。
8. バックエンドは `lifespan` で vibe-server の起動・文字起こしモデルの準備・LLM の準備を並行して行い、すべて終わるか15分たつと
   `startup_complete` を真にする。画面は起動オーバーレイを出して `/health` を1秒ごとに確認する。
9. 終了時はミューテックスを先に解放し、サーバーを止めてから vibe-server を停止する。

テスト専用の環境変数: `VOICE2DOC_TEST_IGNORE_UI_READY=1`・`VOICE2DOC_TEST_STARTUP_DELAY=<秒>`。

### LLM の GPU 実行（Vulkan）
- 依存は Vulkan 版の `llama-cpp-python==0.3.36`（`requirements.txt` の `--extra-index-url`）。`ggml-vulkan.dll` は
  Vulkan ローダー `vulkan-1.dll` を必須で読み込む。ローダーは GPU ドライバーが各 PC に入れるもので、**当アプリは再配布しない**
  （ライセンス上の懸念を避けるため）。代わりに同じバージョンの CPU 版ライブラリを `scripts/fetch_cpu_llama_lib.py` で取得し、
  `voice2doc.spec` が `llama_cpp/lib_cpu` として同梱する。起動時（`llama_cpp` を読み込む前）に `vulkan-1.dll` が読み込めない PC では、
  `LLAMA_CPP_LIB_PATH` を `lib_cpu` に設定して CPU 版に切り替える（`local_llm.prepare_llama_cpp_library`。ログに警告を残す）。
- `LlamaCppClient._load_model`: `llm_device` が `cpu` なら CPU（`n_gpu_layers=0`）。それ以外は `llama_supports_gpu_offload()` が真なら
  全層を GPU（`n_gpu_layers=-1`）で読み込む。GPU での読み込み・生成が例外になったら、警告ログを残して CPU で読み込み直す。
  使った装置と GPU 名はログと `GET /llm/status` に出す。
- 強制終了の検知: GPU で読み込む直前に `%LOCALAPPDATA%\Voice2Doc\llm_gpu_probe` を作り、読み込みと最初の生成が成功したら消す。
  印が残っていれば前回 GPU で強制終了したとみなし、`auto` のときは自動で CPU にする（`gpu` を選んで成功すれば印が消え、`auto` でも GPU に戻る）。
- Whisper（vibe-server）は iGPU を使うが、処理は「文字起こし → LLM」の順なので同時には使わない。

## 6. APIエンドポイント

### 状態
| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/health` | `{status, input_dir, output_dir, whisper_backend, whisper_model, vibe_running, vibe_port, startup_complete, startup_message, wizard_completed, app_version, app, instance_id, safe_render}` |
| POST | `/ui-ready` | 画面が初期化できた合図 `{instance_id}`。不一致なら400 |
| POST | `/safe-render/reset` | 安全モードの目印を消し、次の起動から通常の描画に戻す |
| GET | `/status` | 処理の進捗 `{is_processing, current_file, progress, total_files, completed_files, message, current_step, current_step_name, step_progress, current_output, estimated_remaining, audio_duration, …}` |
| GET | `/prereq-check` | 前提条件の実結果 `{vc_runtime, webview2, ffmpeg, vibe, disk_space, gpu}`（ウィザードが表示する） |
| POST | `/vibe/activate` | Vibe 導入後の再確認（再起動不要）。リクエスト `{path?}`、応答 `{status, message, path, searched[]}`。下記「Vibe の有効化」 |

### ファイル
| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/upload` | ファイルアップロード（multipart）。同じ要求内の同名ファイルは最初の1つだけ保存し、残りは `errors` に返す |
| GET | `/files` | `input/` のファイル一覧（`done.log` に記録済みのものを除く） |
| DELETE | `/files/{filename}` / `/files` | 1件 / 全件削除 |

### 処理
| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/process` | 処理開始（`files`, `prompt`, `prompt_name`, `whisper_model`, `text_input`）。`files` の重複は順序を保って除く |
| POST | `/process/cancel` | 実行中の処理を中止。音声認識中は vibe-server を止めて待機を打ち切り（次の処理で再起動）、AI 生成はチャンクごとに確認する。中止された `/process` は `status="cancelled"` を返し、結果ファイルを作らない |
| POST | `/open-output-folder` | 出力フォルダをエクスプローラーで開く |

### 処理履歴
| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/history` | 出力フォルダ（プロンプト別の保存先を含む）の `.md` 一覧。各項目に `id`=`<保存先フォルダ>::<ファイル名>`。`q` でファイル名・本文の全文検索 |
| GET | `/history/{filename}` | 1件の本文 |
| DELETE | `/history/{filename}` / `/history` | 1件 / 全件を履歴から非表示（ファイルは消さない） |
| DELETE | `/history/{filename}/file` | `.md` と同名の `.txt` を削除 |
| POST | `/history/hide` / `/history/delete-files` | 選んだ複数件 `{"ids": [...]}` を非表示 / 削除。1件でも不正な id なら全体を400で拒否 |
| POST | `/history/{filename}/download?format=txt\|docx\|pdf\|md` | 指定形式に変換して `~\Downloads` に保存 |

### 設定・プロンプト
| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/config` | 設定（`ui_language`, `builtin_prompt_names`, `saved_prompts`, `active_prompt_name`, `vibe_model`, `vibe_server_path`, `llm_model_path`, `llm_model_default_label`, `download_format`, `download_formats`, `dark_mode` など） |
| POST | `/config` | 設定更新。`ui_language` は `ja`/`en` 以外400（切替時は編集していない初期プロンプトと既定の音声認識言語も入れ替わる）。`llm_model_path` が実在する `.gguf` でなければエラーを返し、同じ要求の他の項目も保存しない |
| GET | `/llm/status` | LLM の実行装置の状態 `{configured, device, device_name, reason, gpu_available, gpu_disabled_by_crash}`。`device` は実際に使っている装置（`gpu` / `cpu`。モデル読み込み前は `null`）、`device_name` は GPU 名、`reason` は CPU になった理由（`cpu_selected` / `no_gpu` / `previous_crash` / `gpu_load_failed`）。`llm_device` を変えて保存すると、再起動なしでバックグラウンドで読み込み直し、その結果がここに出る |
| GET | `/status` の `vibe_model_notice` | 設定の文字起こしモデルを使えなかったときの案内文（日英。空なら無し）。取得済みの別のモデルで動いている場合、または vibe-server.exe があるのにモデルのせいで Python 版になった場合に入る（この場合は「vibe-server.exe が見つからない」案内とダイアログは出さない） |
| GET | `/vibe/model/status?model=` | 文字起こしモデルがこの PC にあるか `{model, status, app_downloading, size_mb}`。`status` は `ready`（取得済み）/ `downloading`（未完成）/ `missing`（未取得）。名前が不正なら400 |
| POST | `/vibe/model/download` | `{model}`。利用者が選んだ文字起こしモデルのダウンロードをバックグラウンドで始める（進捗は `/status`）。取得済み・実行中なら何もしない。既知のモデル名以外は400。完了時、そのモデルが設定のモデルなら vibe-server を新しいモデルで起動し直す。**処理時・起動時にモデルを自動ダウンロードすることはない** |
| POST | `/config` の `vibe_model` | 文字起こしモデル（`tiny` / `base` / `small` / `medium` / `large-v3` / `large-v3-turbo`）を変えて保存すると、再起動なしで反映する。vibe-server はモデルを起動時にしか指定できないため、「準備済み」の印を戻して、設定のモデルのファイルが完成していなければ（未完成も含む）ダウンロードし（既定の turbo 以外のモデルも取得する）、新しいモデルで起動し直す（`vibe_client.start_vibe` が稼働中のモデルとの違いを検出して再起動する）。処理中は止めず、次の処理から反映する |
| POST | `/config/reset` | 初期値に戻す（`tab`: `prompt` / `model` / `all`）。初期プロンプトは現在の表示言語のもの |
| GET | `/model/info` | Whisper モデル情報 |
| GET / POST | `/prompts` | 保存済みプロンプトの一覧 / 保存（`name, content, description, output_dir, include_transcript, hidden`。省略した項目は既存の値を保つ） |
| DELETE | `/prompts/{name}` | 削除（初期プロンプトは日英どちらの名前でも削除不可）。選択中だった場合は文章校正（Proofreading）に戻す |
| POST | `/prompts/backup` | すべてを `~\Downloads\Voice2Doc-prompts-YYYYMMDD-HHMMSS.json` に保存 |
| POST | `/prompts/restore/preview` / `/prompts/restore` | バックアップの検証（件数・上書きされる名前） / 復元（`merge`=追加と上書き、`replace`=置き換え。初期プロンプトは残す） |

### Vibe の有効化（`POST /vibe/activate`）
Vibe を入れたあとにアプリを再起動しなくても `vibe-server` を使えるようにする。リクエスト `VibeActivateRequest {path?: string}`
（`path` は手動指定の場所。実在し、ファイル名が `vibe-server.exe` のときだけ `vibe_server_path` に保存して使う）。応答 `VibeActivateResponse`:

| `status` | 意味 |
|---------|------|
| `not_found` | vibe-server.exe が見つからない。`searched` に探した場所の一覧（ログにも出す）。手動指定の `path` が実在しないときもこれ |
| `model_downloading` | 見つかったが、文字起こしモデルが未完成（サイズ不足・`.part` 等の一時ファイルあり）または無い（無いときはアプリ側で取得を始める）。起動しない。画面は数秒ごとに再試行する |
| `starting` | 起動時の初期化がまだ続いている。しばらくしてから再試行 |
| `error` | vibe-server の起動に失敗（`message` に理由） |
| `ok` | 使える。見つけた場所を `vibe_server_path` に保存し、未稼働なら起動して `vibe_fallback_active` を戻し、`_vibe_ready` を更新する。すでに稼働中なら起動し直さない |

呼び出し元: ウィザードの前提条件チェック（`/prereq-check` で vibe が見つかったとき）と手動指定、メイン画面の警告ダイアログの「再確認」、
`/wizard/save`（Vibe が見つかっていれば、保存後にバックグラウンドで呼ぶ）。`whisper_backend` が `vibe` 以外のときは起動せず `ok` を返す。

### ウィザード・その他
| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/wizard/status` | ウィザードの完了状態 |
| POST | `/wizard/save` | `ui_language, whisper_backend, whisper_model, whisper_language` を保存し `wizard_completed=true` |
| POST | `/dialog/pick` | `{kind: "exe"\|"gguf"\|"folder", initial?}` で OS の選択ダイアログを開く |
| POST | `/open-url` | 外部リンクを既定のブラウザで開く。`https` で、ホストが `github.com`・`www.8asue.com`・`www.youtube.com` のときだけ |
| GET | `/{full_path}` | `src/` の静的ファイル（無ければ `index.html`） |

## 7. 処理フロー（/process）

### A. 音声・動画ファイル
1. **音声抽出**（動画のみ）: `ffmpeg -i <入力> -vn -acodec pcm_s16le -ar 16000 -ac 1 -y <一時WAV>`。音声長は `ffprobe` があれば使い、
   無ければ `ffmpeg -i` の `Duration:` から求める（30秒で打ち切り）。
2. **音声認識**: vibe-server.exe（`POST /v1/audio/transcriptions`）。言語は設定の `whisper_language`（`auto`・空は自動検出）。
   日本語のときは句読点入りの例文をヒントとして渡す（拒否されたらヒント無しで1回だけ再試行）。
3. **文字起こしの整形**: `transcript_format.format_transcript()` が句読点・改行・段落を補う（語句は変えない）。日本語（仮名・漢字が
   10%以上）は文末表現の後で区切り、それ以外は英語の規則（文末記号の後で改行、略語では区切らない）。
4. **文章生成**: 選んだプロンプトを指示として、整形後のテキストとファイル名を LLM に渡す（temperature 0.3、最大4096トークン、
   ストリーミング）。
5. **保存**: 名前は `{元ファイル名}-{H1タイトル}`（H1 が無ければ元ファイル名のみ。禁止文字は `_`、タイトルは40文字まで、
   パス全体が260文字に収まるよう切り詰め）。`{保存先}/{名前}.md` に結果（`include_transcript` が真なら文字起こしも埋め込む）、
   `{保存先}/{名前}.txt` に文字起こしを保存する。同名があれば `.md`・`.txt` に共通の連番を付ける。
6. **処理済みの記録**: 元ファイルは `input/` に残し、`done.log` に記録する（`/files` の一覧から外れる）。

### B. テキストファイル（.txt / .md）
音声認識を省き、内容をそのまま LLM に渡す。結果は `{保存先}/{元のファイル名}.md`。処理済みの記録は A と同じ。

### C. テキストの直接入力
入力をそのまま LLM に渡し、`YYYY-MM-DD_HHmm[-H1タイトル].md` に保存する。入力した本文も同じ名前の `.txt` に保存する
（履歴からの削除で両方消える）。`include_transcript` は使わない。生成の失敗・中止時はどちらも保存しない。

## 8. 対応ファイル形式

| カテゴリ | 拡張子 |
|---------|--------|
| 音声 | .wav, .mp3, .aac, .m4a, .ogg, .flac |
| 動画 | .mp4, .avi, .mov, .mkv, .wmv, .flv, .webm, .m4v |
| テキスト | .txt, .md |

1回に最大10ファイル・合計1GBまで。同じファイル（名前・サイズ・更新日時が同じ）を何度入れても1回だけ処理する。名前だけ同じで
内容が違うファイルは警告して除く。

## 9. 設定項目（config.json）

| 項目 | 既定値 | 説明 |
|------|-------------|------|
| `ui_language` | 初回ウィザードが OS の言語で決める（日本語以外は `en`） | 表示言語（`ja` / `en`）。画面・API のメッセージ・初期プロンプトの言語 |
| `whisper_backend` | `"vibe"` | 常に `vibe`（読み込み時にも `vibe` にする）。`vibe-server.exe` が見つからないときだけ、実行中に限り `python` を使う |
| `whisper_model` | `"turbo"` | openai-whisper のモデル（予備の音声認識用） |
| `whisper_language` | `ja`（英語表示の既定は `auto`） | 音声認識の言語。`auto`・空は自動検出 |
| `vibe_model` | `"large-v3-turbo"` | vibe-server の文字起こしモデル。この PC に無いモデルは自動ではダウンロードしない（設定の保存時に確認ダイアログを出し、利用者が「ダウンロード」を選んだときだけ取得する） |
| `vibe_server_path` | `""` | `vibe-server.exe` の手動指定（空なら自動検出） |
| `llm_model_path` | `""` | 「AIモデルの指定」。任意の `.gguf` のフルパス。空・既定ラベル `gemma4:e2b-qat(llama.cpp)` は既定モデル（初回に自動ダウンロード） |
| `llm_device` | `"auto"` | 「LLM の実行装置」。`auto`（GPU が使えれば GPU、使えなければ CPU）/ `gpu`（GPU を優先。使えなければ CPU）/ `cpu`（常に CPU）。それ以外の値は `POST /config` で400（`config.json` を手編集して不正値が入っていても `auto` として扱う） |
| `wizard_completed` | `false` | 初回ウィザードの完了 |
| `dark_mode` | `false` | ダークモード |
| `download_format` | `"txt"` | 履歴からのダウンロード形式の既定（`txt`/`docx`/`pdf`/`md`） |
| `prompt` / `active_prompt_name` | 文章校正（英語は Proofreading） | 選択中のプロンプトの本文と名前 |
| `saved_prompts` | 下記の4つ | `{名前: {content, description, output_dir, include_transcript, hidden}}`。`hidden` は処理メニューに出さない |
| `max_files` / `max_total_size_bytes` | 10 / 1GB | アップロードの上限 |
| `allowed_extensions` | 8章の形式 | 対応拡張子（保存値は既定と合算） |

**初期プロンプト**（`prompt_sets.py`。並び順どおりに表示）

| 日本語 | 英語 | 内容 |
|------|------|------|
| 文章校正 | Proofreading | 要約せず、誤字脱字・文法・不自然な表現を直し、H1 のタイトルと H2 の章に整える（既定） |
| 企画書 | Proposal | 企画タイトル・背景・課題設定・企画概要・ターゲット・ベネフィット・実施内容・KPI・リスク対策の8項目で企画書を作る |
| 報告書 | Report | 校正したうえで、上司に提出する報告書（H1 のタイトル、H2 の報告事項）を作る |
| お客様面談記録 | Customer meeting notes | 年月日・お客様名・部署名・面談者・件名・要約・問題点・課題点・ToDo・ネクストアクションに整理する |

- 表示言語を切り替えると、内容を編集していない初期プロンプトだけを新しい言語のものへ入れ替える（保存先・文字起こし埋め込み・
  `hidden` は引き継ぐ。編集済み、または切替先の名前を利用者が既に使っているものは変えない）。選択中の初期プロンプトも追従する。
- 「初期値に戻す」（プロンプト）は、現在の表示言語の4つと、選択中＝文章校正（Proofreading）に戻す。
- **vibe-server.exe の検出順**（`vibe_client.detect_vibe_binary()`。`find_vibe_binary()` はその先頭の値）: `vibe_server_path` →
  `%LOCALAPPDATA%\vibe\vibe-server.exe` → `%LOCALAPPDATA%\Programs\vibe\vibe-server.exe` → 作業フォルダの `vibe-server.exe` →
  Vibe のアンインストール登録（`HKCU`、続いて `HKLM`（64/32ビット）の `Software\Microsoft\Windows\CurrentVersion\Uninstall\vibe` の
  `InstallLocation`）→ `Program Files\vibe`・`Program Files (x86)\vibe` → スタートメニューの Vibe のショートカットの参照先。
  探した場所の一覧はログと `/vibe/activate` の応答に出す。`/vibe/activate` で見つけた場所は `vibe_server_path` に保存して次回から優先し、
  消えていれば自動検出に戻る。起動は `serve --port 0 <model>` で、
  標準出力の `{"status":"ready","port":N}` を待つ。`setup_checker.check_vibe()` も同じ検出を使う。
- **FFmpeg の検出**（`vibe_client.find_ffmpeg()`）: 検出した `vibe-server.exe` と同じフォルダの `ffmpeg.exe` → PATH の `ffmpeg`。
  openai-whisper を使うときは `add_vibe_to_path()` が Vibe のフォルダを PATH の先頭に加える。

## 10. フロントエンド

### 画面構成（上部ヘッダー）
白いヘッダー（左=題名、右=状態・画面切替ボタン・バージョン・「福栄ラボ」リンク）。アクセントは紫（`#6c4cf1`、ダークは `#8b73f5`）。
1. **処理メニュー**: 説明行（「使用する処理を選択してください」）の右に、いま選んでいる文字起こしモデル名とAIモデル名を表示する（起動時・
   メニューを開いたとき・設定の保存後に更新）。プロンプトカード（名前＋説明）を選ぶ → ファイル入力（「ファイル」/「テキスト」タブ）→ 処理開始。
   カードへファイルを直接ドロップすることもできる。プロンプト選択中は「最近の処理結果」（5件）と「エラー」を表示する。
2. **処理履歴**: 2行表示の一覧（1行目=ファイル名、2行目=日時・サイズ）、チェックボックスでの一括「履歴削除」「ファイル削除」、
   全文検索、詳細モーダル（コピー・形式を選んでダウンロード）、「処理結果の保存フォルダーを開く」。
3. **設定**: プロンプト設定 / モデル設定 / 環境設定 / 情報 のタブ。

処理中にメニューを押すと「処理続行」/「中止して移動」の確認を出す。処理中は画面中央のダイアログに進捗バー・ステップ名・
推定/経過時間・AI 生成のストリーミング・「処理を中止」（Esc と同じ）を表示する。

### 設定タブ
- **プロンプト設定**: 一覧・新規作成・📋コピー（本文をクリップボードへ、かつ「〈名前〉コピー」として追加）・削除（初期プロンプトは不可）・
  出力先フォルダ・「文字起こし結果を含める」・説明・「このプロンプトを使わない」・バックアップ/復元。
- **モデル設定**: 音声認識は vibe-server.exe 固定（Vibe へのリンク付き）。「文字起こしモデル」（`vibe_model`。変えて「保存」したとき、そのモデルが
  この PC に無ければ、`GET /vibe/model/status` で確認して「音声認識モデルをダウンロードします。ダウンロードに時間が必要です。」の
  ダイアログを出す。「ダウンロード」で `POST /vibe/model/download` を呼んでから保存し、「キャンセル」なら保存しない）、
  「vibe-server.exe のパス」（任意、選択ボタン）、「AIモデルの指定」（`.gguf`、選択ボタン）、「LLM の実行装置」（自動（推奨）/ GPU / CPU の選択と、実際に使っている装置の表示。前回 GPU で強制終了して自動で CPU にしているときは、一度だけ案内を出す）。
- **環境設定**: 言語 / Language（変更のたびに保存して再描画）、ライト/ダーク、ダウンロード形式の既定、安全モードの注記と解除ボタン。
- **情報**: バージョン・ライセンス（MIT）・GitHub・福栄ラボ・YouTube・必要なソフトウェア（Vibe。モデルは初回にダウンロード）と、
  同梱していないソフトは各配布元のライセンスに従う旨。リンクは `POST /open-url` で開く。
- プロンプト設定・モデル設定は下部の共通フッター（「このタブを既定値に戻す」「保存」）で保存する。入力があったタブには「●」を付け、
  未保存のまま移動するときは確認する。`Ctrl+S` は表示中のタブを保存する。
- `vibe-server.exe` が見つからず openai-whisper で動いているときは、Vibe の導入を促す警告ダイアログ（`vibe_fallback_active`）を出す。
  ダイアログの「再確認」は `POST /vibe/activate` を呼び、成功すればダイアログとバナーを閉じる（再起動不要）。

### 初回起動ウィザード
ようこそ（最初に必要なもの＝Vibe の枠、「まず右下の『次へ』を押してください」）・言語（OS の言語が既定）→ 前提条件チェック
（`/prereq-check`。vibe-server.exe が無ければ Vibe の案内・インストーラーのあるページへのリンク・「再確認」・
「vibe-server.exe の場所を指定する」。vibe が見つかれば `/vibe/activate` を呼ぶ。4項目（VC++・WebView2・FFmpeg・vibe-server）が
すべて「準備完了」になるまで「次へ」は押せず、上級者向けに小さな「確認せずに進む」リンクがある）→ 文字起こしモデルの選択
（tiny / base / small / Large V3 Turbo（推奨・既定）。同じモデルのカードを2枚並べない。選んだ値は vibe-server のモデル名
`vibe_model` として保存し、Python 版用の `whisper_model` は `large-v3-turbo`→`turbo`、他は同名にする）。ページ3から「次へ」で進むとき、
選んだモデルがこの PC に無ければ（`GET /vibe/model/status`）、「音声認識モデルをダウンロードします。ダウンロードに時間が必要です。」の
ダイアログを出す（「ダウンロード」で `POST /vibe/model/download` を呼んで進む。「キャンセル」ならこのページに残る。進捗は完了後の処理メニューの
バナーに出る）→ AI（文章生成）の説明 → 設定完了。完了時に `/wizard/save`（`vibe_model` を含む。モデルが変わったときは vibe-server を
新しいモデルで起動し直す）を呼び、本画面へ移る。

### 多言語化
- 画面は `src/i18n.js`（辞書は JSON。`t(key, ...args)`、`data-i18n` / `data-i18n-html` / `data-i18n-placeholder` / `data-i18n-title` /
  `data-i18n-aria-label`）、API・スプラッシュ・メッセージボックスは `src_backends/i18n.py`（`t()` / `tr(lang, key)`）。
  表示言語は `localStorage` の `voice2doc.ui_language` にも保存する。
- 日本語のまま残すもの: ログ・コメント・docstring・本画面の `<title>`「話書き - Voice2Doc」（二重起動時の前面化に使う）。
- インストーラーは OS の表示言語が日本語なら日本語、それ以外は英語で表示する（`GetUserDefaultUILanguage`、English を先に登録）。
  完了ページは日英とも、音声認識に Vibe を使うことと、未導入なら初回起動時に導入することを案内し、「Voice2Doc を起動する」
  チェック（既定でオン。`MUI_FINISHPAGE_RUN`）を置く（Vibe のページは開かない。Vibe の案内は初回起動ウィザードが行う）。
  Vibe の案内（ウィザード・情報タブ・README）には、ダウンロードするファイル名の例（2026年11月13日時点の Intel・AMD の
  Windows 用は `vibe_3.3.2_x64-setup.exe`。数字の部分は版で変わる）を添える。

## 11. 推定時間・進捗
- **音声認識**: 予測秒数 = 音声長 × 学習係数 + 固定の準備時間（動画の音声抽出5秒、openai-whisper のモデル読み込み3秒）。
  係数の初期値は vibe-server 0.15・openai-whisper 0.4 で、認識が終わるたびに実測から指数移動平均で学習する（5秒未満の音声は学習しない）。
  予測を超えたら「推定時間を超過しています（経過 m:ss）」。
- **AI 生成**: 出力の長さを予測できないため、推定は出さず経過時間を表示する。
- 全体の進捗率は、ファイル数とファイル内の重み付きステップ（音声抽出・モデル読み込み・文字起こし・生成）から求める。

## 12. ダウンロード（出力形式）
- 履歴の実体は常に Markdown。ダウンロード時に変換して `~\Downloads` に `<元の名前>_<タイトル>.<拡張子>` で保存する（同名は連番）。
- **txt**: Markdown 記号を除き、箇条書きは `・`。UTF-8 BOM・CRLF。
- **docx**（python-docx）: 見出し1〜3・箇条書き（3階層）・太字・表、游ゴシック。
- **PDF**（fpdf2）: A4・余白20mm、IPAex ゴシックを埋め込み（太字は擬似太字、未収録の文字は `?`）。日本語の禁則処理はしない。
  見出しは、折り返した行数と直後の内容（本文2行、または表の見出し行と最初の行）がページに残るときだけ置き、入らなければ改ページする。
  半ページを超える見出しは途中で改ページする。
- XML で使えない文字（制御文字、U+FFFE・U+FFFF など）は、txt・docx・PDF のどれでも取り除く。
- **表**: 見出し行（`|` を含む行）の次が区切り行（`|` を含み、各セルがダッシュ1個以上。`:` で揃え）のとき表とみなし、`|` を含まない行・
  見出し・リスト・引用・コードフェンスで終える。前後の `|` は省略でき、リスト直下の字下げした表も認識する。`\|` は文字の `|`、`<br>` は
  セル内改行、列数は最も多い行に揃える（足りないセルは空）。本文に繰り返された区切り行（ダッシュ2個以上）は捨てる。
  コードフェンス内と区切り行の無い `|` の行は表にしない。
  - docx・PDF: 全セルに黒の細罫線、見出し行は太字＋背景 F2F2F2、ページをまたぐと見出し行を繰り返す。列幅は文字量に比例し、本文の幅に収める。
    列が多いときは文字を小さくする（5pt まで）。
  - docx: 表が続くときと文書の末尾には空の段落を置く。Word の上限の63列を超える分は最後の列へまとめる。
  - PDF: fpdf2 の `table()` は使わず自前で描く。1ページより高い行は分割し、半ページを超える見出し行は繰り返さない。
  - 画面（履歴のプレビュー・処理結果）: `app.js` の `markdownTable` が同じ規則で表にする。セルの文字は HTML エスケープする。
  - txt: 1行を ` | ` でつないで出力する（セル内改行は半角スペース、区切り行は出さない）。md は無変換。

## 13. アクティビティログ
- 起動ごとに `logs/YYYY-MM-DDTHH-MM-SS.log` を作り、最新30件を残す。書式は `時刻.ミリ秒 | レベル | スレッド | ロガー | メッセージ`。
- 環境情報・前提条件・vibe-server の検出/起動/停止・処理の開始/終了（ファイル名・サイズ・音声長・モデル・所要時間）・設定変更（項目名のみ）・
  エラーのトレースバック・未処理例外（`sys.excepthook` / `threading.excepthook` / `faulthandler`）を記録する。
- **プロンプト本文・文字起こし本文はログに出さない**（文字数のみ）。

## 14. 既知の制限
| 項目 | 内容 |
|------|------|
| アンインストール | インストール先 `%LOCALAPPDATA%\Voice2Doc` をフォルダごと消すため、同じ場所の設定・履歴・モデルも消える |
| 音声認識中の進捗 | vibe-server の呼び出し中は 0% / 100% のみ（推定時間は時計で減らす） |
| PDF | 日本語の禁則処理なし、リストの入れ子は6段まで |
| 表 | セルの結合・表内のリストは未対応 |
| コード署名 | インストーラーは未署名のため、SmartScreen の警告が出ることがある。Windows 11 のスマート アプリ コントロールが有効な PC では実行がブロックされ、オフにしないと導入できない |

## 15. テスト・ビルド
```bash
python -m pytest src_backends/ -v                 # 全テスト
python scripts/ruff_baseline.py                   # ruff の指摘が増えていないか（CI と同じ）
build_installer.bat                               # PyInstaller + NSIS → dist\Voice2Doc-Setup.exe
```
- 外部依存（vibe-server・LLM・ダウンロード）は `conftest.py` のフィクスチャでモックする。
- `test_i18n.py`: 日英の辞書のキー・置換子の一致、英語に日本語が無いこと、HTML・`app.js`・`wizard.js` に直書きの日本語が無いこと、
  スプラッシュ・`main.py` のメッセージボックスが辞書を通ること。
- `test_docs_consistency.py`: 公開するファイルに、使っていない仕組み・旧称の語が無いこと。
- `test_version.py`: `APP_VERSION`・インストーラー・`CHANGELOG.md` の版の一致。
- タグ `v*` の push で `.github/workflows/release.yml` が Windows でインストーラーを作り、Release の下書きに添付する（[docs/RELEASING.md](docs/RELEASING.md)）。
