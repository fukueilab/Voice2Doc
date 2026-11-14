"""バックエンドの表示言語（日本語/英語）と、画面へ返すメッセージの辞書。

画面に出るメッセージ（処理の進捗・API の応答・エラー）だけを対象にする。ログ(logger)・コメント・docstring は
日本語のまま。表示言語は設定 ``ui_language`` で、``config`` の読み込み・保存時に ``set_language()`` で反映される。
未知のキーはキー名をそのまま返し、未知の言語は日本語へフォールバックする。
"""
from typing import Dict

SUPPORTED_LANGUAGES = ("ja", "en")
DEFAULT_LANGUAGE = "ja"

_language = DEFAULT_LANGUAGE


def normalize_language(lang) -> str:
    """対応言語ならそのまま、そうでなければ日本語を返す"""
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def set_language(lang) -> None:
    global _language
    _language = normalize_language(lang)


def get_language() -> str:
    return _language


MESSAGES: Dict[str, Dict[str, str]] = {
    "ja": {
        "srv.starting": "サーバーを起動しています...",
        "model.preparing": "モデルを準備しています（初回起動時は数分かかる場合があります）...",
        "vibe.model_dl": "文字起こしモデル '{model}' をダウンロード中…初回のみ時間がかかる場合があります",
        "vibe.starting": "vibe-serverを起動中...",
        "vibe.starting_step": "vibe-server 起動中",
        "llm.dl": (
            "LLMモデルをダウンロード中…これは一度だけです。"
            "回線状況により5分から10分必要です"
        ),
        "llm.dl_step": "LLMモデル ダウンロード中",
        "prereq.error": "前提条件チェック中にエラーが発生しました",
        "file.invalid_name": "不正なファイル名",
        "file.not_found": "ファイルが見つかりません",
        "file.unsupported": "対応外の形式: {ext}",
        "file.too_large": "ファイルサイズが大きすぎます",
        "file.duplicate": "同じ名前のファイルが重複しているため無視しました",
        "file.read_failed": "ファイルの読み込みに失敗しました",
        "proc.starting": "処理を開始します...",
        "proc.done": "処理が完了しました",
        "proc.cancelling": "処理を中止します",
        "proc.not_running": "処理中ではありません",
        "proc.cancelled": "処理を中止しました",
        "proc.busy": "処理中です。しばらくお待ちください。",
        "proc.lingering": "前の処理を終了しています。しばらくお待ちください。",
        "proc.files_done": "{n}ファイルを処理しました",
        "step.ai": "AI処理中... {name}{counter}",
        "step.ai_prompt": "AI処理中... ({prompt})",
        "step.text_processing": "テキスト入力を処理中...",
        "step.convert": "音声・動画変換中... {name}{counter}",
        "step.model": "モデル準備中... {name}{counter}",
        "step.transcribe": "音声認識中... {name}{counter}",
        "step.read_text": "テキスト読み込み中... {name}{counter}",
        "step.read_text_msg": "{name} テキストを読み込み中...",
        "step.generating": "{prompt} 生成中...",
        "step.generated": "{prompt} 生成完了",
        "step.saving": "結果を保存中...",
        "out.transcript_heading": "音声認識結果",
        "cfg.saved": "設定を保存しました",
        "cfg.reset": "設定をデフォルトに戻しました",
        "cfg.save_failed": "保存に失敗しました: {error}",
        "cfg.reset_failed": "リセットに失敗しました: {error}",
        "cfg.invalid_language": "不正な言語です: {value}",
        "cfg.invalid_llm_device": "LLM の実行装置が不正です（auto / gpu / cpu のいずれか）: {value}",
        "cfg.invalid_vibe_model": "文字起こしモデルの名前が不正です: {value}",
        "wizard.saved": "ウィザード設定を保存しました",
        "prompt.saved": "プロンプト「{name}」を保存しました",
        "prompt.deleted": "プロンプト「{name}」を削除しました",
        "prompt.not_found": "プロンプト「{name}」が見つかりません",
        "prompt.builtin_undeletable": "初期プロンプトは削除できません",
        "prompt.default_label": "プロンプト",
        "dlg.all_files": "すべてのファイル (*.*)",
        "dlg.gguf": "GGUFモデル (*.gguf)",
        "dlg.prompts_json": "プロンプトのバックアップ (*.json)",
        "backup.saved": "ダウンロードフォルダに保存しました: {path}",
        "backup.write_failed": "バックアップを保存できませんでした: {error}",
        "backup.read_failed": "バックアップファイルを読み込めませんでした: {error}",
        "backup.file_not_found": "バックアップファイルが見つかりません",
        "backup.too_large": "ファイルが大きすぎます（{size}MBまで）",
        "backup.invalid_json": "JSON形式のファイルではありません",
        "backup.not_a_backup": "話書きのプロンプトのバックアップファイルではありません",
        "backup.invalid_name": "プロンプト名が空、または長すぎるものがあります",
        "backup.invalid_prompt": "プロンプト「{name}」の内容が正しくありません",
        "backup.empty": "バックアップにプロンプトがありません",
        "backup.invalid_mode": "復元方法が正しくありません: {mode}",
        "backup.restored": "{n}件のプロンプトを復元しました",
        "dlg.cannot_open": "ダイアログを開けませんでした。パスを直接入力してください",
        "open_url.not_allowed": "このリンクは開けません（https の決まったサイトのみ開けます）",
        "hist.no_selection": "対象が選択されていません",
        "conv.failed": "{format}への変換に失敗しました: {error}",
        "llm.user_message": (
            "音声の文字起こし内容:\n{transcript}\n\n"
            "上記の音声内容を元に、指定された項目で作成してください。\n"
            "年月日は音声ファイル名から推察してください。"
        ),
        "llm.user_message_named": (
            "音声ファイル名: {filename}\n\n音声の文字起こし内容:\n{transcript}\n\n"
            "上記の音声内容を元に、指定された項目で作成してください。\n"
            "年月日は音声ファイル名から推察してください。"
        ),
        "eta.hm": "{h}時間{m}分",
        "eta.ms": "{m}分{s}秒",
        "eta.s": "{s}秒",
        "eta.ai_elapsed": "AI処理 経過時間: {t}",
        "eta.estimate": "推定: {t}",
        "eta.over": "推定時間を超過しています（経過 {t}）",
        "vibe.server_missing": (
            "vibe-server.exeが見つかりません。"
            "Vibe（https://github.com/thewh1teagle/vibe/releases）をインストールするか、"
            "設定で vibe-server.exe のパスを指定してください。"
        ),
        "vibe.activate_ok": "Vibe を認識しました。音声認識を使えます。",
        "vibe.activate_not_found": (
            "vibe-server.exe が見つかりません。Vibe のインストールが完了しているか確認してください。"
        ),
        "vibe.activate_bad_path": "指定された場所に vibe-server.exe がありません: {path}",
        "vibe.activate_downloading": (
            "Vibe が音声認識モデルをダウンロード中です。"
            "完了するまでお待ちください（Vibe の画面は、完了後に × で閉じて構いません）。"
        ),
        "vibe.activate_starting": "アプリが音声認識を準備中です。少し待ってからもう一度お試しください。",
        "vibe.activate_failed": "vibe-server を起動できませんでした: {error}",
        "vibe.start_failed": "vibe-serverプロセスの起動に失敗しました: {error}",
        "vibe.crashed": "vibe-serverプロセスが異常終了しました (code={code}): {stderr}",
        "vibe.start_timeout": "vibe-serverの起動がタイムアウトしました (30秒)",
        "vibe.model_missing": "文字起こしモデル '{model}' が見つかりません",
        "vibe.model_file_missing": "文字起こしモデル '{model}' のファイルがありません（ダウンロードが済んでいません）",
        "vibe.model_fallback": (
            "文字起こしモデル '{wanted}' を使えなかったため、取得済みの '{using}' で動作しています。"
            "設定の「文字起こしモデル」を確認してください。"
        ),
        "vibe.model_not_downloaded": (
            "文字起こしモデル '{wanted}' はまだダウンロードされていないため、取得済みの '{using}' で動作しています。"
            "設定で文字起こしモデルを保存し直し、「ダウンロード」を選んでください。"
        ),
        "vibe.model_not_downloaded_python": (
            "文字起こしモデル '{model}' がダウンロードされていないため、代替の音声認識（Python版）で動作しています。"
            "設定で文字起こしモデルを保存し直し、「ダウンロード」を選んでください。"
        ),
        "vibe.model_failed_python": (
            "文字起こしモデル '{model}' を準備できなかったため、代替の音声認識（Python版）で動作しています。"
            "ネットワークの接続や空き容量を確認し、設定で文字起こしモデルを選び直してください。"
        ),
        "vibe.size_mismatch": "ダウンロードサイズが一致しません（期待値: {expected}バイト, 実際: {actual}バイト）",
        "vibe.model_dl_failed": "モデル '{model}' のダウンロードに失敗しました",
        "vibe.model_dl_failed_err": "モデル '{model}' のダウンロードに失敗しました: {error}",
        "vibe.restart_failed": "vibe-serverの再起動に失敗しました: {error}",
        "vibe.not_running": "vibe-serverが稼働していません",
        "vibe.api_timeout": "vibe-server APIタイムアウト (10分)",
        "vibe.busy": "vibe-server: 同時に複数の文字起こしは実行できません",
        "vibe.api_error": "vibe-server APIエラー: {status} {text}",
        "rec.extracting": "動画から音声を抽出中...",
        "rec.extracted": "音声抽出完了",
        "rec.transcribing": "音声認識中...",
        "rec.transcribed": "音声認識完了",
        "rec.model_loading": "モデルを読み込み中...",
        "rec.model_ready": "モデル準備完了",
        "rec.file_not_found": "ファイルが見つかりません: {path}",
        "rec.unsupported_model": "サポートされていないモデル: {model}. 利用可能: {available}",
        "rec.extract_failed": "音声抽出に失敗しました: {error}",
        "rec.no_ffmpeg": (
            "FFmpegが見つかりません。Vibe（ffmpeg.exe を含みます）をインストールするか、"
            "ffmpeg を PATH に通してください。"
        ),
        "llm.model_dl": "LLMモデルをダウンロード中...",
        "llm.model_file_missing": "モデルファイルが見つかりません: {path}",
        "llm.unavailable": "llama-cpp-pythonが利用できません",
        "llm.unavailable_err": "llama-cpp-python が利用できません: {error}",
        "cfg.gguf_required": "AIモデルには .gguf ファイルのパスを指定してください",
        "cfg.gguf_missing": "指定されたAIモデルのファイルが見つかりません: {path}",
        "boot.starting": "起動しています…",
        "boot.starting_hint": "初回や環境によっては数十秒かかることがあります。そのままお待ちください。",
        "boot.failed_title": "起動できませんでした",
        "boot.retry": "もう一度試す",
        "boot.log_location": "詳しい記録（ログ）: {path}",
        "boot.err_port_full": (
            "通信に使うポート（8008〜8030）がすべて他のソフトに使われています。"
            "他のソフトを終了してから、もう一度お試しください。"
        ),
        "boot.err_server_died": "内部サーバーを起動できませんでした（{error}）。",
        "boot.err_timeout": "内部サーバーの起動が時間内に終わりませんでした。",
        "boot.err_generic": "起動中にエラーが発生しました: {error}",
        "boot.err_webview": "画面を表示する部品（WebView2）を起動できませんでした: {error}",
        "boot.msgbox_title": "Voice2Doc - 起動エラー",
        "boot.wizard_title": "話書き - 初回設定ウィザード",
        "boot.blank_title": "Voice2Doc - 画面を表示できません",
        "boot.blank_body": (
            "画面を表示できませんでした。再読み込みと、描画の安全モードでの再起動も試しましたが直りませんでした。\n\n"
            "次をお試しください。\n"
            "・パソコンを再起動する\n"
            "・Microsoft Edge WebView2 Runtime を再インストールする\n"
            "・グラフィックスドライバーを更新する\n\n"
            "改善しない場合は、次のフォルダーのログを添えて報告してください。\n{path}"
        ),
    },
    "en": {
        "srv.starting": "Starting the server...",
        "model.preparing": "Preparing the models (the first start can take a few minutes)...",
        "vibe.model_dl": "Downloading the transcription model '{model}'... This takes a while the first time only",
        "vibe.starting": "Starting vibe-server...",
        "vibe.starting_step": "Starting vibe-server",
        "llm.dl": (
            "Downloading the LLM model... This happens only once "
            "and can take 5 to 10 minutes depending on your connection"
        ),
        "llm.dl_step": "Downloading the LLM model",
        "prereq.error": "An error occurred while checking the prerequisites",
        "file.invalid_name": "Invalid file name",
        "file.not_found": "File not found",
        "file.unsupported": "Unsupported format: {ext}",
        "file.too_large": "The file is too large",
        "file.duplicate": "Ignored because a file with the same name was already included",
        "file.read_failed": "Failed to read the file",
        "proc.starting": "Starting...",
        "proc.done": "Processing is complete",
        "proc.cancelling": "Cancelling processing",
        "proc.not_running": "Nothing is being processed",
        "proc.cancelled": "Processing was cancelled",
        "proc.busy": "Processing is in progress. Please wait.",
        "proc.lingering": "Finishing the previous run. Please wait.",
        "proc.files_done": "Processed {n} files",
        "step.ai": "AI processing... {name}{counter}",
        "step.ai_prompt": "AI processing... ({prompt})",
        "step.text_processing": "Processing the text input...",
        "step.convert": "Converting audio/video... {name}{counter}",
        "step.model": "Preparing the model... {name}{counter}",
        "step.transcribe": "Recognizing speech... {name}{counter}",
        "step.read_text": "Reading text... {name}{counter}",
        "step.read_text_msg": "Reading text from {name}...",
        "step.generating": "Generating {prompt}...",
        "step.generated": "{prompt} generated",
        "step.saving": "Saving the result...",
        "out.transcript_heading": "Transcript",
        "cfg.saved": "Settings saved",
        "cfg.reset": "Settings were reset to the defaults",
        "cfg.save_failed": "Failed to save: {error}",
        "cfg.reset_failed": "Failed to reset: {error}",
        "cfg.invalid_language": "Invalid language: {value}",
        "cfg.invalid_llm_device": "Invalid LLM device (use auto, gpu or cpu): {value}",
        "cfg.invalid_vibe_model": "Invalid transcription model name: {value}",
        "wizard.saved": "Wizard settings saved",
        "prompt.saved": "Saved prompt \"{name}\"",
        "prompt.deleted": "Deleted prompt \"{name}\"",
        "prompt.not_found": "Prompt \"{name}\" not found",
        "prompt.builtin_undeletable": "Built-in prompts cannot be deleted",
        "prompt.default_label": "Prompt",
        "dlg.all_files": "All files (*.*)",
        "dlg.gguf": "GGUF models (*.gguf)",
        "dlg.prompts_json": "Prompt backups (*.json)",
        "backup.saved": "Saved to the Downloads folder: {path}",
        "backup.write_failed": "Could not save the backup: {error}",
        "backup.read_failed": "Could not read the backup file: {error}",
        "backup.file_not_found": "The backup file was not found",
        "backup.too_large": "The file is too large (up to {size} MB)",
        "backup.invalid_json": "The file is not in JSON format",
        "backup.not_a_backup": "The file is not a Voice2Doc prompt backup",
        "backup.invalid_name": "A prompt name is empty or too long",
        "backup.invalid_prompt": "The prompt \"{name}\" is not valid",
        "backup.empty": "The backup contains no prompts",
        "backup.invalid_mode": "Invalid restore option: {mode}",
        "backup.restored": "Restored {n} prompts",
        "dlg.cannot_open": "Could not open the dialog. Enter the path directly",
        "open_url.not_allowed": "This link cannot be opened (only https links to known sites are allowed)",
        "hist.no_selection": "No items are selected",
        "conv.failed": "Conversion to {format} failed: {error}",
        "llm.user_message": (
            "Transcript of the audio:\n{transcript}\n\n"
            "Based on the audio content above, write it up with the specified items.\n"
            "Infer the date from the audio file name."
        ),
        "llm.user_message_named": (
            "Audio file name: {filename}\n\nTranscript of the audio:\n{transcript}\n\n"
            "Based on the audio content above, write it up with the specified items.\n"
            "Infer the date from the audio file name."
        ),
        "eta.hm": "{h}h {m}m",
        "eta.ms": "{m}m {s}s",
        "eta.s": "{s}s",
        "eta.ai_elapsed": "AI processing elapsed: {t}",
        "eta.estimate": "Estimated: {t}",
        "eta.over": "Over the estimate (elapsed {t})",
        "vibe.server_missing": (
            "vibe-server.exe was not found. Install Vibe (https://github.com/thewh1teagle/vibe/releases) "
            "or set the path to vibe-server.exe in Settings."
        ),
        "vibe.activate_ok": "Vibe was detected. Speech recognition is ready.",
        "vibe.activate_not_found": (
            "vibe-server.exe was not found. Make sure the Vibe installation has finished."
        ),
        "vibe.activate_bad_path": "vibe-server.exe was not found at the specified location: {path}",
        "vibe.activate_downloading": (
            "Vibe is still downloading the speech recognition model. "
            "Please wait until it finishes (you can close the Vibe window with the x afterwards)."
        ),
        "vibe.activate_starting": "The app is still preparing speech recognition. Please try again in a moment.",
        "vibe.activate_failed": "Could not start vibe-server: {error}",
        "vibe.start_failed": "Failed to start the vibe-server process: {error}",
        "vibe.crashed": "The vibe-server process exited abnormally (code={code}): {stderr}",
        "vibe.start_timeout": "Timed out waiting for vibe-server to start (30 s)",
        "vibe.model_missing": "The transcription model '{model}' was not found",
        "vibe.model_file_missing": (
            "The file of the transcription model '{model}' is missing (it has not been downloaded yet)"
        ),
        "vibe.model_fallback": (
            "The transcription model '{wanted}' could not be used, so the downloaded '{using}' is being used. "
            "Check the transcription model in Settings."
        ),
        "vibe.model_not_downloaded": (
            "The transcription model '{wanted}' has not been downloaded yet, "
            "so the downloaded '{using}' is being used. "
            "Save the transcription model in Settings again and choose \"Download\"."
        ),
        "vibe.model_not_downloaded_python": (
            "The transcription model '{model}' has not been downloaded, so the fallback speech recognition "
            "(Python version) is being used. Save the transcription model in Settings again and choose \"Download\"."
        ),
        "vibe.model_failed_python": (
            "The transcription model '{model}' could not be prepared, so the fallback speech recognition "
            "(Python version) is being used. Check your network connection and free disk space, then choose the "
            "transcription model again in Settings."
        ),
        "vibe.size_mismatch": "The download size does not match (expected {expected} bytes, got {actual} bytes)",
        "vibe.model_dl_failed": "Failed to download the model '{model}'",
        "vibe.model_dl_failed_err": "Failed to download the model '{model}': {error}",
        "vibe.restart_failed": "Failed to restart vibe-server: {error}",
        "vibe.not_running": "vibe-server is not running",
        "vibe.api_timeout": "vibe-server API timed out (10 minutes)",
        "vibe.busy": "vibe-server: only one transcription can run at a time",
        "vibe.api_error": "vibe-server API error: {status} {text}",
        "rec.extracting": "Extracting audio from the video...",
        "rec.extracted": "Audio extraction complete",
        "rec.transcribing": "Recognizing speech...",
        "rec.transcribed": "Speech recognition complete",
        "rec.model_loading": "Loading the model...",
        "rec.model_ready": "Model ready",
        "rec.file_not_found": "File not found: {path}",
        "rec.unsupported_model": "Unsupported model: {model}. Available: {available}",
        "rec.extract_failed": "Audio extraction failed: {error}",
        "rec.no_ffmpeg": "FFmpeg was not found. Install Vibe (it includes ffmpeg.exe) or put ffmpeg on your PATH.",
        "llm.model_dl": "Downloading the LLM model...",
        "llm.model_file_missing": "Model file not found: {path}",
        "llm.unavailable": "llama-cpp-python is not available",
        "llm.unavailable_err": "llama-cpp-python is not available: {error}",
        "cfg.gguf_required": "Specify the path to a .gguf file for the AI model",
        "cfg.gguf_missing": "The specified AI model file was not found: {path}",
        "boot.starting": "Starting up...",
        "boot.starting_hint": "This can take tens of seconds on the first run or on slower PCs. Please wait.",
        "boot.failed_title": "Voice2Doc could not start",
        "boot.retry": "Try again",
        "boot.log_location": "Details (log): {path}",
        "boot.err_port_full": (
            "All the ports used for communication (8008-8030) are in use by other programs. "
            "Close them and try again."
        ),
        "boot.err_server_died": "The internal server could not start ({error}).",
        "boot.err_timeout": "The internal server did not start in time.",
        "boot.err_generic": "An error occurred during startup: {error}",
        "boot.err_webview": "The component that draws the window (WebView2) could not start: {error}",
        "boot.msgbox_title": "Voice2Doc - Startup error",
        "boot.wizard_title": "Voice2Doc - Setup wizard",
        "boot.blank_title": "Voice2Doc - Cannot show the window",
        "boot.blank_body": (
            "The window could not be displayed. Reloading and restarting in safe rendering mode did not help.\n\n"
            "Please try:\n"
            "- Restarting your PC\n"
            "- Reinstalling Microsoft Edge WebView2 Runtime\n"
            "- Updating your graphics driver\n\n"
            "If it still does not work, report it with the log from this folder:\n{path}"
        ),
    },
}


def tr(lang, key: str, **kwargs) -> str:
    """指定した言語でメッセージを返す（未知のキーはキー名、未知の言語は日本語）"""
    table = MESSAGES[normalize_language(lang)]
    text = table.get(key)
    if text is None:
        text = MESSAGES[DEFAULT_LANGUAGE].get(key, key)
    return text.format(**kwargs) if kwargs else text


def t(key: str, **kwargs) -> str:
    """現在の表示言語でメッセージを返す"""
    return tr(_language, key, **kwargs)
