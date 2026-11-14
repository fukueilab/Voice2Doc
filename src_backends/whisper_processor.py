"""Whisper音声認識モジュール (動画対応)"""
import os
import re
import subprocess
import tempfile
import threading
import logging
import time
from pathlib import Path
from typing import Optional

import whisper

from .i18n import t
from .path_utils import app_data_dir, get_cache_base_dir, is_frozen

logger = logging.getLogger("voice2doc.whisper")

# モデルキャッシュ先 (PyInstaller対応)
if is_frozen():
    CACHE_DIR = get_cache_base_dir() / ".cache" / "whisper"
else:
    CACHE_DIR = app_data_dir(Path(os.path.expanduser("~")) / "Documents") / ".cache" / "whisper"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 利用可能なモデル
AVAILABLE_MODELS = ["tiny", "base", "small", "medium", "large", "turbo"]

# 対応する動画形式
VIDEO_EXTENSIONS = [".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"]

# モデル読み込み状態
_loaded_model: Optional[whisper.Whisper] = None
_loaded_model_name: Optional[str] = None
# get_model()のcheck-then-setをスレッド間で保護する。executorを起動時準備専用/
# リクエスト処理用に分離したことで、起動時のWhisperモデル準備タスクと最初の/processリクエストの
# 文字起こしが真に並行実行されるようになり、ロックなしでは同一モデルのロード（whisper.load_model()の
# 重複ダウンロード・二重メモリ確保）が同時に走りうるようになったため導入
_model_load_lock = threading.Lock()


def check_model_exists(model_name: str) -> bool:
    """モデルがローカルに存在するか確認"""
    if model_name not in AVAILABLE_MODELS:
        return False
    model_files = list(CACHE_DIR.glob(f"{model_name}*.pt"))
    if model_files:
        logger.info(f"モデル '{model_name}' はキャッシュに存在: {model_files[0]}")
        return True
    default_cache = Path.home() / ".cache" / "whisper"
    if default_cache.exists():
        model_files = list(default_cache.glob(f"{model_name}*.pt"))
        if model_files:
            logger.info(f"モデル '{model_name}' はデフォルトキャッシュに存在: {model_files[0]}")
            return True
    logger.info(f"モデル '{model_name}' はローカルに存在しません（ダウンロードが必要）")
    return False


def get_model(model_name: str = "turbo") -> whisper.Whisper:
    """Whisperモデルを取得（キャッシュあり）"""
    global _loaded_model, _loaded_model_name

    if _loaded_model is not None and _loaded_model_name == model_name:
        logger.info(f"モデル '{model_name}' はキャッシュから使用")
        return _loaded_model

    if model_name not in AVAILABLE_MODELS:
        raise ValueError(t("rec.unsupported_model", model=model_name, available=AVAILABLE_MODELS))

    with _model_load_lock:
        # ロック待機中に他スレッドが読み込み済みの場合はそれを使う（二重ロード防止）
        if _loaded_model is not None and _loaded_model_name == model_name:
            logger.info(f"モデル '{model_name}' はキャッシュから使用（ロック待機後）")
            return _loaded_model

        exists = check_model_exists(model_name)
        if not exists:
            logger.info(f"モデル '{model_name}' をダウンロード中... (初回は数分かかります)")
        else:
            logger.info(f"モデル '{model_name}' を読み込み中...")

        _loaded_model = whisper.load_model(
            model_name,
            download_root=str(CACHE_DIR),
        )
        _loaded_model_name = model_name
        logger.info(f"モデル '{model_name}' の準備完了")
        return _loaded_model


def is_video_file(file_path: str | Path) -> bool:
    """ファイルが動画かどうかを判定"""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def ffmpeg_command() -> str:
    """実行する ffmpeg（Vibe のフォルダの ffmpeg.exe → PATH）。
    見つからなければ "ffmpeg" のまま返す（実行時に FileNotFoundError になる）"""
    from . import vibe_client

    found = vibe_client.find_ffmpeg(vibe_client.configured_binary_path())
    return str(found) if found else "ffmpeg"


# ffmpeg -i の出力にある長さ（例: "Duration: 00:01:02.50,"）
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
# 長さの取得はヘッダーを読むだけなので通常は一瞬で終わる。壊れたファイル等で止まったときに処理全体を待たせない上限
DURATION_TIMEOUT_SEC = 30


def get_audio_duration(file_path: str | Path) -> float:
    """音声/動画ファイルの長さ（秒）。ffprobe があれば使い、無ければ（Vibe には ffprobe が含まれない）
    ffmpeg -i の「Duration:」行から求める。どちらでも取れなければ 0"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=DURATION_TIMEOUT_SEC,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return float(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):  # TimeoutExpired も SubprocessError
        pass
    try:
        # 出力ファイルを指定しないため終了コードは 1 になるが、長さは標準エラーに出る
        result = subprocess.run(
            [ffmpeg_command(), "-hide_banner", "-i", str(file_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=DURATION_TIMEOUT_SEC,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        match = _DURATION_RE.search(stderr)
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (OSError, subprocess.SubprocessError):
        pass
    logger.warning(f"音声長の取得に失敗: {file_path}")
    return 0.0


def extract_audio_from_video(video_path: str | Path, output_path: Optional[str | Path] = None) -> Path:
    """FFmpegを使用して動画から音声を抽出"""
    video_path = Path(video_path)

    if output_path is None:
        output_path = Path(tempfile.gettempdir()) / f"{video_path.stem}_audio.wav"
    else:
        output_path = Path(output_path)

    logger.info(f"動画から音声を抽出中: {video_path.name} -> {output_path.name}")

    cmd = [
        ffmpeg_command(),
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-y",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info(f"音声抽出完了: {output_path.name} ({output_path.stat().st_size} bytes)")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpegエラー: {e.stderr}")
        raise RuntimeError(t("rec.extract_failed", error=e.stderr))
    except FileNotFoundError:
        raise RuntimeError(t("rec.no_ffmpeg"))


def transcribe(
    audio_path: str | Path,
    model_name: str = "turbo",
    language: Optional[str] = "ja",
    progress_callback=None,
    backend: str = "python",
) -> dict:
    """
    音声/動画ファイルを文字起こし

    Args:
        audio_path: 音声/動画ファイルのパス
        model_name: Whisperモデル名
        language: 言語コード (None=自動検出)
        progress_callback: 進捗コールバック関数 (step, message, progress=None)

    Returns:
        dict: {"text": str, "segments": list, "language": str}
    """
    file_path = Path(audio_path)
    if not file_path.exists():
        raise FileNotFoundError(t("rec.file_not_found", path=file_path))

    # vibe-server backend の場合は vibe_client に委譲
    if backend == "vibe":
        from . import vibe_client
        return vibe_client.transcribe(
            audio_path=audio_path,
            model_name=model_name,
            language=language,
            progress_callback=progress_callback,
        )

    # openai-whisper は音声の読み込みに PATH の ffmpeg を使うため、Vibe の ffmpeg.exe を見えるようにする
    from . import vibe_client
    vibe_client.add_vibe_to_path(vibe_client.configured_binary_path())

    temp_audio = None

    try:
        # 動画ファイルの場合は音声を抽出
        if is_video_file(file_path):
            if progress_callback:
                progress_callback("ffmpeg", t("rec.extracting"), 0)
            temp_audio = extract_audio_from_video(file_path)
            audio_to_process = temp_audio
            if progress_callback:
                progress_callback("ffmpeg", t("rec.extracted"), 100)
        else:
            audio_to_process = file_path

        # 音声長を取得
        audio_duration = get_audio_duration(audio_to_process)
        logger.info(f"音声長: {audio_duration:.1f}秒")

        # モデル読み込み
        if progress_callback:
            progress_callback("model", t("rec.model_loading"), 0)
        model = get_model(model_name)
        if progress_callback:
            progress_callback("model", t("rec.model_ready"), 100)

        # 文字起こし
        if progress_callback:
            progress_callback("transcribe", t("rec.transcribing"), 0)
        logger.info(f"文字起こし開始: {file_path.name}")

        try:
            result = model.transcribe(
                str(audio_to_process),
                language=language,
                verbose=False,
            )
        finally:
            pass

        # 進捗を100%に設定
        if progress_callback:
            progress_callback("transcribe", t("rec.transcribed"), 100)

        logger.info(f"文字起こし完了: {file_path.name} ({len(result['text'])}文字)")
        return {
            "text": result["text"],
            "segments": [
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"],
                }
                for seg in result.get("segments", [])
            ],
            "language": result.get("language", "unknown"),
        }

    finally:
        # 一時ファイルを削除
        if temp_audio and temp_audio.exists():
            temp_audio.unlink()
            logger.info(f"一時ファイル削除: {temp_audio.name}")


def get_model_info() -> dict:
    """現在のモデル情報を取得"""
    return {
        "loaded_model": _loaded_model_name,
        "cache_dir": str(CACHE_DIR),
        "available_models": AVAILABLE_MODELS,
    }
