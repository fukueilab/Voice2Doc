"""話書き (Voice2Doc) - Python Sidecar Backend"""
import os
import sys
import json
import re
import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from pydantic import BaseModel

from .config import is_valid_vibe_model, normalize_model_name
from .config import INPUT_DIR, DONE_LOG_FILE, OUTPUT_DIR, CACHE_DIR, USER_DOCS, BASE_DIR, APP_VERSION, DOWNLOAD_FORMATS, DEFAULT_LLM_MODEL_LABEL, LLM_DEVICES, normalize_llm_device, normalize_llm_model_path, load_config, save_config, AppConfig, load_default_config, save_default_config, reset_config, DEFAULT_CONFIG_FILE
from .path_utils import get_app_dir
from . import i18n, prompt_backup, prompt_sets
from .i18n import t
from .whisper_processor import transcribe as whisper_transcribe, get_model_info, AVAILABLE_MODELS, get_model, get_audio_duration, is_video_file
from . import local_llm
from . import vibe_client
from . import setup_checker
from . import eta
from . import startup
from . import output_formats
from .transcript_format import format_transcript

# ログ設定（通常起動ではルートmain.pyが先に app_logging.setup_logging() で
# ファイル出力を設定済みのため、この basicConfig は何もしない。単体import用のフォールバック）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("voice2doc")

# 静的ファイルディレクトリ (PyInstaller対応)
STATIC_DIR = get_app_dir() / "src"

# スレッドプール (リクエスト処理用: Whisper文字起こし・LLM生成・テキストファイル読み込み・前提条件チェック)
executor = ThreadPoolExecutor(max_workers=2)
# スレッドプール (選択ダイアログ専用。ダイアログを開いたままにしても、音声認識・LLM生成の枠を塞がない)
dialog_executor = ThreadPoolExecutor(max_workers=1)
# スレッドプール (起動時準備専用: Whisperモデルダウンロード・LLM準備・vibe-server起動)
# 起動時に3タスクが並行実行されるため、上のexecutorと共有すると同時に2つまでしか進まず、
# 起動直後に来た/processリクエストがWhisper/LLM実行の順番待ちになりうる。用途別に分離する
startup_executor = ThreadPoolExecutor(max_workers=3)

# 処理排他制御
_process_lock = asyncio.Lock()

# 処理状態管理
processing_status = {
    "is_processing": False,
    "is_cancelled": False,
    "is_downloading": False,
    "current_file": None,
    "progress": 0,
    "total_files": 0,
    "completed_files": 0,
    "message": "",
    "current_step": None,
    "current_step_name": "",
    "step_progress": 0,
    "current_output": "",
    "estimated_remaining": None,
    "audio_duration": None,
    # 時間表示用（eta.compute_eta_text。/status応答のたびに現在時刻から計算する）
    "step_started_at": None,           # 現在のステップの開始時刻（AI生成の経過時間表示用）
    "recognition_started_at": None,    # 音声認識（音声抽出〜文字起こし）の開始時刻
    "recognition_predicted_sec": None, # 音声認識全体の予測秒数
    # vibe-server.exeが見つからずPython版whisperへフォールバックして稼働中かどうか。
    # 一度発生したらアプリプロセスの生存期間中は維持する（_start/_finish_processing_statusではリセットしない）
    "vibe_fallback_active": False,
    "vibe_model_notice": "",  # 文字起こしモデルのことで知らせたい文（空なら無し）
    # 利用者が選んだ文字起こしモデルのダウンロードの進捗（処理の進捗 message/step_progress とは別に持つ）
    "vibe_model_downloading": "",  # ダウンロード中のモデル名（空なら無し）
    "vibe_model_progress": 0,
}

# 処理ステップの重み（進捗率・推定残り時間の計算で、ファイル内の
# どのステップまで進んだかを重み付けするために使用。秒換算の相対値で、
# 絶対的な正確さは不要。実測に基づかない近似値のため、必要に応じて調整すること）
STEP_WEIGHT_FFMPEG = 5.0       # 動画の音声抽出（動画ファイルのみ）
STEP_WEIGHT_MODEL = 3.0        # Whisperモデル読み込み（Pythonバックエンドのみ）
TRANSCRIBE_REALTIME_FACTOR_VIBE = 0.15    # vibe-server(whisper.cpp)の音声長に対する概算実行比率
TRANSCRIBE_REALTIME_FACTOR_PYTHON = 0.4   # openai-whisperの概算実行比率
STEP_WEIGHT_TRANSCRIBE_MIN = 5.0          # 音声が短い場合の下限（0除算・過小評価防止）
STEP_WEIGHT_LLM = 15.0         # LLM生成（バックエンド共通の近似値）

# テキスト直接入力パス・テキストファイル入力は単一ステップ（llm）のみなので重み付けの影響を受けない
PROCESS_STEPS_TEXT = (("llm", 1.0),)


def _build_weighted_steps(is_video: bool, whisper_backend: str, audio_duration: float) -> list:
    """このファイルで実際に発生するステップ名と重み（秒換算の相対値）のリストを返す。

    バックエンド（vibe-server/Python）によって発生するステップ自体が異なり（vibe-serverは
    `model`ステップを呼ばない）、`transcribe`の重みは音声長に比例させることで、
    実態に近い進捗率・推定残り時間を計算する。"""
    steps = []
    if is_video:
        steps.append(("ffmpeg", STEP_WEIGHT_FFMPEG))
    if whisper_backend != "vibe":
        steps.append(("model", STEP_WEIGHT_MODEL))
    realtime_factor = (
        TRANSCRIBE_REALTIME_FACTOR_VIBE if whisper_backend == "vibe" else TRANSCRIBE_REALTIME_FACTOR_PYTHON
    )
    transcribe_weight = max(audio_duration * realtime_factor, STEP_WEIGHT_TRANSCRIBE_MIN)
    steps.append(("transcribe", transcribe_weight))
    steps.append(("llm", STEP_WEIGHT_LLM))
    return steps

# 起動状態フラグ
_startup_complete = False
_startup_message = "srv.starting"  # i18nのキー（表示言語は設定の読み込み後に決まるため、返すときに翻訳する。空=表示なし）

# 初期化タスク（Whisperモデル/LLM準備/vibe-server起動）の完了待機タイムアウト秒数
# （テストでは短縮するためモジュール属性として公開）
STARTUP_TASKS_TIMEOUT_SEC = 900  # 15分

# vibe-server サーバー遅延初期化フラグ
_vibe_ready = False
# 設定で文字起こしモデルを変えるたびに増やす。準備を始めたときの値と終了時の値が違えば、その準備は古い設定のものなので
# 「準備済み」にしない（準備の最中にモデルを変えても、変更が失われない）
_vibe_generation = 0
_vibe_init_lock = asyncio.Lock()


def _mark_vibe_ready(generation: int) -> None:
    global _vibe_ready
    if generation == _vibe_generation:
        _vibe_ready = True
_vibe_startup_task = None  # vibe-server起動バックグラウンドタスク


# 文字起こしモデルのダウンロードは、設定で利用者が「ダウンロード」を選んだときだけ行う
# （起動時・処理時には自動で取得しない）。モデル名 → 実行中のダウンロードタスク（二重に始めない）
_vibe_model_download_tasks: dict = {}
_vibe_download_executor = ThreadPoolExecutor(max_workers=1)
_vibe_download_lock = asyncio.Lock()


def _vibe_model_ready(model: str) -> bool:
    """そのモデルのファイルが、この PC に完成した形で存在するか"""
    return vibe_client.find_model_file(model, vibe_client.find_vibe_binary_dir()) is not None


async def _download_vibe_model(model: str) -> None:
    """文字起こしモデルをダウンロードする。

    終わったとき、それが設定のモデルなら、新しいモデルで vibe-server を起動し直す。
    進捗は処理の進捗（message など）を使わず、専用の項目（vibe_model_downloading / vibe_model_progress）で伝える。
    ダウンロードは同時に1つだけ行い、専用のスレッドで動かす（起動用のスレッドを塞がない）。"""
    loop = asyncio.get_event_loop()
    async with _vibe_download_lock:
        if await loop.run_in_executor(startup_executor, _vibe_model_ready, model):
            return

        logger.info(f"文字起こしモデル '{model}' のダウンロードを開始します（利用者の操作）...")
        processing_status["vibe_model_downloading"] = model
        processing_status["vibe_model_progress"] = 0

        def model_progress(msg, prog=None):
            logger.info(f"文字起こしモデルダウンロード: {msg}")
            if prog is not None:
                processing_status["vibe_model_progress"] = int(prog)

        try:
            await loop.run_in_executor(
                _vibe_download_executor,
                lambda: vibe_client.ensure_model_downloaded(model, progress_callback=model_progress),
            )
            logger.info(f"文字起こしモデル '{model}' のダウンロードが完了しました")
            if normalize_model_name(load_config().vibe_model) == normalize_model_name(model):
                processing_status["vibe_model_notice"] = ""
                _apply_vibe_model_change()  # 設定のモデルになったので、新しいモデルで vibe-server を起動し直す
        except Exception as e:  # noqa: BLE001
            logger.error(f"文字起こしモデル '{model}' のダウンロードに失敗しました: {e}")
            processing_status["vibe_model_notice"] = t("vibe.model_dl_failed", model=model)
        finally:
            processing_status["vibe_model_downloading"] = ""
            processing_status["vibe_model_progress"] = 0


def _start_vibe_model_download(model: str) -> None:
    """ダウンロードをバックグラウンドで始める（同じモデルが実行中なら何もしない）"""
    task = _vibe_model_download_tasks.get(model)
    if task is not None and not task.done():
        return
    _vibe_model_download_tasks[model] = asyncio.get_event_loop().create_task(_download_vibe_model(model))


def _vibe_model_usable(config: AppConfig) -> bool:
    """vibe-server.exe があり、使えるモデル（設定のもの、無ければ取得済みの別のもの）がこの PC にあるか"""
    bin_dir = vibe_client.find_vibe_binary_dir()
    if bin_dir is None:
        return False
    return bool(_vibe_model_ready(config.vibe_model) or vibe_client.ready_models(bin_dir))


async def ensure_vibe_ready():
    """vibe-serverの起動・準備完了を保証（起動時にバックグラウンドで起動済み）"""
    global _vibe_ready

    if _vibe_ready and processing_status.get("vibe_fallback_active"):
        # Python 版で動いている間に、モデルが後から用意された（Vibe のダウンロードが終わった等）なら vibe-server に戻す
        if await asyncio.get_event_loop().run_in_executor(startup_executor, _vibe_model_usable, load_config()):
            logger.info("使えるモデルが用意されたため、vibe-server での音声認識に戻します")
            _vibe_ready = False

    if _vibe_ready:
        return

    # 起動時のバックグラウンドタスクが完了するのを待機
    if _vibe_startup_task is not None and not _vibe_startup_task.done():
        logger.info("vibe-serverの起動完了を待機中...")
        await _vibe_startup_task

    if _vibe_ready:
        return

    # タスクが失敗した場合や未起動の場合、フォールバック
    async with _vibe_init_lock:
        if _vibe_ready:
            return

        config = load_config()
        generation = _vibe_generation

        if config.whisper_backend != "vibe":
            _mark_vibe_ready(generation)
            return

        processing_status["message"] = t("vibe.starting")
        processing_status["current_step"] = "vibe_startup"
        processing_status["current_step_name"] = t("vibe.starting_step")

        try:
            logger.info("vibe-server起動中（フォールバック初期化）...")
            loop = asyncio.get_event_loop()

            logger.info(f"文字起こしモデル '{config.vibe_model}' の準備中...")
            await _start_vibe_server(config, loop)
            logger.info("vibe-serverの準備完了")
            processing_status["vibe_fallback_active"] = False  # 起動できたので Python 版には切り替えない

            if not vibe_client.is_vibe_running():
                logger.warning("vibe-serverが起動後に停止しました。再起動します...")
                def do_restart():
                    vibe_client.stop_vibe()
                    vibe_client.start_vibe(
                        model_name=config.vibe_model,
                        binary_path=config.vibe_server_path,
                    )
                await loop.run_in_executor(startup_executor, do_restart)
                logger.info("vibe-serverの再起動完了")

            _mark_vibe_ready(generation)

        except Exception as e:
            logger.error(f"vibe-serverの起動に失敗しました: {e}")
            logger.warning("Python whisper バックエンドにフォールバックします（このプロセスの実行中のみ。"
                            "設定ファイルは変更せず、次回起動時にも改めてvibe-server.exeの検出を試みる）")
            # processing_statusのフラグで「現在フォールバック稼働中」をフロントエンドへ伝え、
            # _process_one_file()側でも実際に使うバックエンドをこのフラグで判断する。
            _fall_back_to_python(e, config, generation)

        finally:
            processing_status["message"] = ""
            processing_status["current_step"] = None
            processing_status["current_step_name"] = ""


async def _start_vibe_server(config: AppConfig, loop, binary_checked: bool = False) -> None:
    """設定の文字起こしモデルで vibe-server を起動する。モデルは自動ではダウンロードしない。

    設定のモデルがこの PC に無い・起動できないときは、取得済みの別のモデルで起動し、画面に知らせる
    （vibe_model_notice）。使えるモデルが無ければ例外にする（呼び出し側が Python 版へフォールバックする）。
    vibe-server.exe 自体が無いときは、モデルの話ではないので、そのまま例外にする。"""
    processing_status["vibe_model_notice"] = ""
    if not binary_checked:
        bin_dir = await loop.run_in_executor(startup_executor, vibe_client.find_vibe_binary_dir)
        if bin_dir is None:
            raise RuntimeError(t("vibe.server_missing"))
    try:
        await loop.run_in_executor(
            startup_executor,
            lambda: vibe_client.start_vibe(model_name=config.vibe_model, binary_path=config.vibe_server_path),
        )
        return
    except Exception as e:  # noqa: BLE001
        error = e
    logger.warning(f"文字起こしモデル '{config.vibe_model}' で vibe-server を起動できません: {error}")
    bin_dir = await loop.run_in_executor(startup_executor, vibe_client.find_vibe_binary_dir)
    downloaded = await loop.run_in_executor(startup_executor, _vibe_model_ready, config.vibe_model)
    for name in await loop.run_in_executor(
            startup_executor, lambda: vibe_client.ready_models(bin_dir, exclude=config.vibe_model)):
        try:
            await loop.run_in_executor(
                startup_executor,
                lambda n=name: vibe_client.start_vibe(model_name=n, binary_path=config.vibe_server_path),
            )
        except Exception as e2:  # noqa: BLE001
            logger.warning(f"取得済みのモデル '{name}' でも起動できません: {e2}")
            continue
        key = "vibe.model_fallback" if downloaded else "vibe.model_not_downloaded"
        processing_status["vibe_model_notice"] = t(key, wanted=config.vibe_model, using=name)
        logger.info(f"取得済みのモデル '{name}' で vibe-server を起動しました")
        return
    raise error


def _fall_back_to_python(error: Exception, config: AppConfig, generation: Optional[int] = None) -> None:
    """vibe-server を使えないときの Python 版 whisper への切り替え（このプロセスの実行中のみ）。
    vibe-server.exe があるのにモデルで失敗した場合は、「vibe-server.exe が見つからない」とは案内しない"""
    global _vibe_ready
    if generation is None:
        _vibe_ready = True
    else:
        _mark_vibe_ready(generation)
    processing_status["vibe_fallback_active"] = True
    if vibe_client.find_vibe_binary(config.vibe_server_path or "") is not None:
        key = "vibe.model_failed_python" if _vibe_model_ready(config.vibe_model) else "vibe.model_not_downloaded_python"
        processing_status["vibe_model_notice"] = t(key, model=config.vibe_model)


async def activate_vibe(manual_path: Optional[str] = None) -> "VibeActivateResponse":
    """Vibe導入後に、アプリを再起動せずに vibe-server を使える状態にする。

    vibe-server.exeを探し直し（設定のパス→自動検出）、モデルが完成していれば起動して、
    起動時に立てたフォールバックの印（vibe_fallback_active）を戻す。
    すでに稼働中なら何もしない（二重に起動しない）。"""
    global _vibe_ready
    loop = asyncio.get_event_loop()
    config = load_config()

    if manual_path is not None and manual_path.strip():
        manual = Path(manual_path.strip().strip('"'))
        # vibe.exe（Vibe 本体の画面）などを選ばれると、起動しようとして待たされるため、ファイル名も確かめる
        if not manual.is_file() or manual.name.lower() != "vibe-server.exe":
            return VibeActivateResponse(
                status="not_found", message=t("vibe.activate_bad_path", path=str(manual)),
                searched=[f"manual: {manual}"],
            )
        config.vibe_server_path = str(manual)
        save_config(config)
        logger.info(f"vibe-server.exeの場所を保存しました（手動指定）: {manual}")

    binary, searched = await loop.run_in_executor(
        executor, lambda: vibe_client.detect_vibe_binary(config.vibe_server_path or "", deep=True)
    )
    if binary is None:
        return VibeActivateResponse(status="not_found", message=t("vibe.activate_not_found"), searched=searched)

    # 見つけた場所を保存し、次回からはまずそこを使う（消えていたら自動検出に戻る）
    if config.vibe_server_path != str(binary):
        config.vibe_server_path = str(binary)
        try:
            save_config(config)
            logger.info(f"vibe-server.exeの場所を保存しました: {binary}")
        except Exception as e:  # noqa: BLE001 - 保存に失敗しても起動は続ける
            logger.warning(f"vibe-server.exeの場所の保存に失敗: {e}")

    base = dict(path=str(binary), searched=searched)

    if config.whisper_backend != "vibe":
        return VibeActivateResponse(status="ok", message=t("vibe.activate_ok"), **base)

    # 起動時の初期化がまだ続いているときは、そちらと二重に起動しないよう待ってもらう
    if _vibe_startup_task is not None and not _vibe_startup_task.done():
        return VibeActivateResponse(status="starting", message=t("vibe.activate_starting"), **base)

    async with _vibe_init_lock:
        generation = _vibe_generation
        if vibe_client.is_vibe_running():
            _mark_vibe_ready(generation)
            processing_status["vibe_fallback_active"] = False
            running = vibe_client.running_model_name()
            other_model = isinstance(running, str) and (
                normalize_model_name(running) != normalize_model_name(config.vibe_model))
            if not other_model:
                processing_status["vibe_model_notice"] = ""  # 別のモデルで動いている間は、その案内を残す
            return VibeActivateResponse(status="ok", message=t("vibe.activate_ok"), **base)

        model_state = await loop.run_in_executor(
            executor, vibe_client.model_status, config.vibe_model, Path(binary).parent
        )
        if model_state != "ready":
            # モデルは自動ではダウンロードしない（Vibe 自身のダウンロードを待つ。アプリでの取得は設定で利用者が選ぶ）。
            # ただし、設定のモデルが無くても、取得済みの別のモデルがあれば、それで起動する
            others = [] if model_state == "downloading" else await loop.run_in_executor(
                startup_executor, lambda: vibe_client.ready_models(Path(binary).parent, exclude=config.vibe_model))
            if not others:
                logger.info(f"文字起こしモデル '{config.vibe_model}' が未完成のため起動を見送ります ({model_state})")
                return VibeActivateResponse(status="model_downloading", message=t("vibe.activate_downloading"), **base)

        try:
            # 設定のモデルで起動する。無ければ取得済みの別のモデルで起動し、案内（vibe_model_notice）を出す
            await _start_vibe_server(config, loop, binary_checked=True)
        except Exception as e:  # noqa: BLE001
            logger.error(f"vibe-serverの起動に失敗しました（再確認）: {e}")
            return VibeActivateResponse(status="error", message=t("vibe.activate_failed", error=e), **base)

        _mark_vibe_ready(generation)
        processing_status["vibe_fallback_active"] = False
        logger.info("vibe-serverを起動し、フォールバックを解除しました（再起動不要）")
        return VibeActivateResponse(status="ok", message=t("vibe.activate_ok"), **base)


_background_tasks: set = set()


async def _activate_vibe_quietly() -> None:
    """ウィザード完了時のバックグラウンド有効化（失敗してもログに残すだけ）"""
    try:
        await activate_vibe()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ウィザード完了時のvibe-server有効化に失敗: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 50)
    logger.info("話書き Sidecar 起動")
    logger.info(f"入力フォルダ: {INPUT_DIR}")
    logger.info(f"出力フォルダ: {OUTPUT_DIR}")
    logger.info(f"処理済みログ: {DONE_LOG_FILE}")
    logger.info(f"設定ファイル: {INPUT_DIR.parent / 'config.json'}")

    # 設定を読み込み
    config = load_config()
    logger.info(f"音声認識バックエンド: {config.whisper_backend}")
    logger.info(f"Whisperモデル: {config.whisper_model}")

    logger.info(f"AIモデル: {config.llm_model_path or DEFAULT_LLM_MODEL_LABEL}")

    # default.jsonが存在しない場合は自動生成
    if not DEFAULT_CONFIG_FILE.exists():
        try:
            save_default_config()
            logger.info("default.json を自動生成しました")
        except Exception as e:
            logger.warning(f"default.json の自動生成に失敗: {e}")

    global _startup_complete, _startup_message
    # 前回のlifespan（同一プロセス内の再起動・テスト）の完了フラグを引き継がない
    _startup_complete = False
    _startup_message = "model.preparing"
    logger.info("バックグラウンド初期化タスクを開始")

    # Python Whisper モデルの自動ダウンロード（バックグラウンド）
    async def download_whisper_model():
        if config.whisper_backend != "python":
            return
        try:
            logger.info(f"Whisperモデル '{config.whisper_model}' の存在確認中...")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                startup_executor,
                lambda: get_model(config.whisper_model)
            )
            logger.info(f"Whisperモデル '{config.whisper_model}' の準備完了")
        except Exception as e:
            logger.warning(f"Whisperモデルのダウンロードに失敗（初回起動時に再試行）: {e}")

    whisper_startup_task = asyncio.create_task(download_whisper_model())

    # ローカルLLMの準備（バックグラウンド、llama-cpp-python用モデルのダウンロード）
    async def prepare_local_llm():
        # ユーザーが任意のAIモデル(.gguf)を指定している場合は既定モデルのダウンロードは不要
        if load_config().llm_model_path:
            logger.info("AIモデルが指定されているため、既定モデルのダウンロードをスキップします")
            return
        try:
            logger.info("llama-cpp-python用モデルの存在確認中...")
            loop = asyncio.get_event_loop()

            # ダウンロード中フラグを設定
            processing_status["is_processing"] = True
            processing_status["is_downloading"] = True
            processing_status["message"] = t("llm.dl")
            processing_status["current_step"] = "llm_download"
            processing_status["current_step_name"] = t("llm.dl_step")

            def llm_progress(msg, prog=None):
                logger.info(f"LLM: {msg}")
                if prog is not None:
                    processing_status["step_progress"] = prog

            try:
                await loop.run_in_executor(
                    startup_executor,
                    lambda: local_llm.ensure_model_downloaded(
                        progress_callback=llm_progress
                    )
                )
                logger.info("llama-cpp-python用モデルの準備完了")
            finally:
                # ダウンロード完了後、ステータスをリセット
                processing_status["is_processing"] = False
                processing_status["is_downloading"] = False
                processing_status["message"] = ""
                processing_status["current_step"] = None
                processing_status["current_step_name"] = ""
                processing_status["step_progress"] = 0
        except Exception as e:
            logger.warning(f"ローカルLLMの準備に失敗: {e}")
            processing_status["message"] = ""
            processing_status["current_step"] = None
            processing_status["current_step_name"] = ""

    llm_startup_task = asyncio.create_task(prepare_local_llm())

    # vibe-serverの準備（バックグラウンド）
    async def prepare_vibe():
        global _vibe_ready
        generation = _vibe_generation
        try:
            config = load_config()
            if config.whisper_backend != "vibe":
                _mark_vibe_ready(generation)
                return

            logger.info("vibe-server起動中（起動時初期化）...")
            loop = asyncio.get_event_loop()

            # vibe-server.exe が無いときは、大きなモデルを先に取得しても使えないので取得しない
            # （Vibe 導入後は /vibe/activate で取得・起動する）
            if await loop.run_in_executor(
                startup_executor, vibe_client.find_vibe_binary, config.vibe_server_path or ""
            ) is None:
                raise RuntimeError(t("vibe.server_missing"))

            # モデル準備（未取得ならダウンロード）とサーバー起動
            await _start_vibe_server(config, loop, binary_checked=True)
            logger.info("vibe-serverの準備完了")
            processing_status["vibe_fallback_active"] = False
            _mark_vibe_ready(generation)

        except Exception as e:
            logger.warning(f"vibe-serverの起動に失敗（Python版whisperへフォールバックします）: {e}")
            _fall_back_to_python(e, load_config(), generation)

    global _vibe_startup_task
    _vibe_startup_task = asyncio.create_task(prepare_vibe())

    # 3つの初期化タスクがすべて完了してから起動完了とする
    # （health応答自体はyield直後から可能なので、この待機はサーバーの疎通性を妨げない）
    # ネットワーク不調等でタスクが長時間終わらない場合に備え、上限時間で強制的に起動完了扱いにする
    # （フロントエンドのwaitForApi待機上限より短く設定し、オーバーレイが確実に閉じるようにする）
    async def mark_startup_complete():
        global _startup_complete, _startup_message
        # asyncio.wait_for(gather(...)) はタイムアウト時にgather自身をキャンセルし、
        # それが子タスク（vibe-server起動等）にも伝播してしまう。子タスクは中断させず
        # バックグラウンドで継続させたいので、キャンセルを伝播しないasyncio.waitを使う
        _, pending = await asyncio.wait(
            [whisper_startup_task, llm_startup_task, _vibe_startup_task],
            timeout=STARTUP_TASKS_TIMEOUT_SEC,
        )
        if pending:
            logger.warning(
                f"初期化タスクが{STARTUP_TASKS_TIMEOUT_SEC}秒以内に完了しませんでした。"
                "起動完了として続行します（未完了のタスクはバックグラウンドで継続）"
            )
        else:
            logger.info("起動完了（モデル準備含む）")
        _startup_complete = True
        _startup_message = ""

    asyncio.create_task(mark_startup_complete())

    yield

    # vibe-server 停止
    if vibe_client.is_vibe_running():
        vibe_client.stop_vibe()

    logger.info("話書き Sidecar 終了")


app = FastAPI(
    title="Voice2Doc Sidecar",
    version=APP_VERSION,
    lifespan=lifespan,
)

def configure_cors(port: int) -> None:
    """CORSの許可元を、実際に使うポートの 127.0.0.1 / localhost だけにする（サーバー起動前に呼ぶ）。

    8008が他のソフトに使われていて別のポートへ移った場合にも、画面からのAPI呼び出しが拒否されないようにする。
    """
    from starlette.middleware import Middleware

    cors = Middleware(
        CORSMiddleware,
        allow_origins=startup.build_origins(port),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # add_middleware は起動後に呼べないため、一覧を直接差し替えて、次の要求でミドルウェアを組み直させる
    app.user_middleware = [cors] + [m for m in app.user_middleware if m.cls is not CORSMiddleware]
    app.middleware_stack = None


configure_cors(startup.DEFAULT_PORT)


# ==================== Pydantic Models ====================

class HealthResponse(BaseModel):
    status: str
    input_dir: str
    output_dir: str
    whisper_backend: str
    whisper_model: str
    vibe_running: bool
    vibe_port: Optional[int] = None
    startup_complete: bool = False
    startup_message: str = ""
    wizard_completed: bool = False
    app_version: str = APP_VERSION
    # 自分のサーバーかの識別（別のプロセスが同じポートで応答していても取り違えない）
    app: str = startup.APP_ID
    instance_id: str = startup.INSTANCE_ID
    # GPUを使わない描画の安全モード（白い画面の自動復旧で有効になる）かどうか
    safe_render: bool = False


class FileItem(BaseModel):
    name: str
    size: int
    status: str


class ProcessRequest(BaseModel):
    files: list[str]
    prompt: Optional[str] = None
    prompt_name: Optional[str] = None
    whisper_model: Optional[str] = None
    text_input: Optional[str] = None


class ProcessResponse(BaseModel):
    status: str
    message: str
    results: list[dict]


class ConfigUpdateRequest(BaseModel):
    whisper_backend: Optional[str] = None
    whisper_model: Optional[str] = None
    whisper_language: Optional[str] = None
    prompt: Optional[str] = None
    dark_mode: Optional[bool] = None
    download_format: Optional[str] = None
    active_prompt_name: Optional[str] = None
    vibe_server_path: Optional[str] = None
    vibe_model: Optional[str] = None
    llm_model_path: Optional[str] = None
    llm_device: Optional[str] = None  # "auto" / "gpu" / "cpu"
    ui_language: Optional[str] = None


class PromptRequest(BaseModel):
    name: str
    content: Optional[str] = None
    description: Optional[str] = None
    output_dir: Optional[str] = None
    include_transcript: Optional[bool] = None
    hidden: Optional[bool] = None  # True なら処理メニューに表示しない（「このプロンプトを使わない」）


class PromptRestoreRequest(BaseModel):
    path: Optional[str] = None  # 選択ダイアログで選んだバックアップファイル
    content: Optional[str] = None  # ブラウザ単体で開いたときなど、画面で読み込んだファイルの内容
    mode: Optional[str] = None  # "merge"（追加＋同名は上書き）/ "replace"（全て置き換え）。previewでは不要


class ConfigResetRequest(BaseModel):
    tab: str = "all"  # "prompt", "model", "all"


class DialogPickRequest(BaseModel):
    # "exe"（vibe-server.exe）/ "gguf"（AIモデル）/ "folder"（フォルダ）/ "prompts_json"（プロンプトのバックアップ）
    kind: str
    initial: Optional[str] = None  # ダイアログの初期位置（入力欄に入っている値）


class StatusResponse(BaseModel):
    is_processing: bool
    is_downloading: bool = False
    current_file: Optional[str]
    progress: int
    total_files: int
    completed_files: int
    message: str
    current_step: Optional[str] = None
    current_step_name: Optional[str] = None
    step_progress: Optional[int] = None
    current_output: Optional[str] = None
    estimated_remaining: Optional[str] = None
    audio_duration: Optional[float] = None
    vibe_fallback_active: bool = False
    vibe_model_notice: str = ""  # 設定の文字起こしモデルを使えなかったときの案内（日英）
    vibe_model_downloading: str = ""  # ダウンロード中の文字起こしモデル名
    vibe_model_progress: int = 0  # そのダウンロードの進捗（%）


class VibeActivateRequest(BaseModel):
    # 手動で指定された vibe-server.exe のパス（省略時は設定→自動検出の順に探す）
    path: Optional[str] = None


class VibeActivateResponse(BaseModel):
    # ok / not_found / model_downloading / starting / error
    status: str
    message: str = ""
    path: str = ""  # 見つかった vibe-server.exe のパス
    searched: list[str] = []  # 探した場所の一覧（見つからなかったときの原因調べ用）


# ==================== Path Traversal Prevention ====================

def sanitize_filename(filename: str) -> str:
    """Remove path traversal sequences from a filename."""
    # Strip directory components
    name = os.path.basename(filename)
    # Remove null bytes
    name = name.replace("\x00", "")
    return name


def is_safe_filename(filename: str) -> bool:
    """Check if a filename is safe (no traversal, absolute paths, or special chars)."""
    if not filename:
        return False
    # Reject null bytes
    if "\x00" in filename:
        return False
    # Reject absolute paths
    if os.path.isabs(filename):
        return False
    # Reject traversal sequences
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    return True


# ==================== API Endpoints ====================

@app.get("/health", response_model=HealthResponse)
async def health():
    config = load_config()
    return HealthResponse(
        status="ok",
        input_dir=str(INPUT_DIR),
        output_dir=str(OUTPUT_DIR),
        whisper_backend=config.whisper_backend,
        whisper_model=config.whisper_model,
        vibe_running=vibe_client.is_vibe_running(),
        vibe_port=vibe_client.get_vibe_port(),
        startup_complete=_startup_complete,
        startup_message=t(_startup_message) if _startup_message else "",
        wizard_completed=config.wizard_completed,
        safe_render=startup.safe_render_requested(),
    )


class UiReadyRequest(BaseModel):
    instance_id: str


@app.post("/ui-ready")
async def ui_ready(request: UiReadyRequest):
    """画面が初期化できたことの合図。起動直後の白い画面の検知（見張り役）が待っている。

    起動ごとの instance_id が一致しない要求（前回のページ・別のアプリ）は受け付けない。
    """
    if request.instance_id != startup.INSTANCE_ID:
        raise HTTPException(status_code=400, detail="instance_id mismatch")
    if not startup.ignore_ui_ready_for_test():
        startup.mark_ui_ready()
    return {"status": "ok"}


@app.post("/safe-render/reset")
async def safe_render_reset():
    """描画の安全モード（GPU無効）を解除する。次の起動から通常の描画に戻る"""
    if not startup.clear_safe_render_marker():
        raise HTTPException(status_code=500, detail="failed to remove marker")
    return {"status": "ok", "safe_render": False}


@app.get("/prereq-check")
async def prereq_check():
    """前提条件（VC++ Runtime/WebView2/FFmpeg/vibe-server/ディスク容量）の実際のチェック結果を返す（初回起動ウィザード用）"""
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(executor, setup_checker.run_all_checks)
        return results
    except Exception as e:
        logger.error(f"前提条件チェックエラー: {e}")
        raise HTTPException(status_code=500, detail=t("prereq.error"))


@app.post("/vibe/activate", response_model=VibeActivateResponse)
async def vibe_activate(request: Optional[VibeActivateRequest] = None):
    """Vibe導入後の再確認: vibe-server.exeを探し直し、使える状態にする（再起動不要）。
    path を渡すと、手動指定された vibe-server.exe の場所を保存して使う"""
    return await activate_vibe(request.path if request else None)


@app.get("/status", response_model=StatusResponse)
async def get_status():
    status = dict(processing_status)
    # 推定時間(音声認識)・経過時間(AI生成)は、コールバックの有無に関わらず応答のたびに計算する
    status["estimated_remaining"] = (
        eta.compute_eta_text(status, time.time()) if status.get("is_processing") else None
    )
    return StatusResponse(**status)


@app.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    config = load_config()
    uploaded = []
    errors = []

    for file in files:
        safe_name = sanitize_filename(file.filename)
        if not is_safe_filename(safe_name):
            errors.append({"file": file.filename, "error": t("file.invalid_name")})
            continue

        # 同じ要求の中の同名ファイルは最初の1つだけ保存する（後のものによる上書き・取り違えを防ぐ）
        # Windows は大文字小文字を区別しないので casefold で比べる
        if safe_name.casefold() in {u.casefold() for u in uploaded}:
            errors.append({"file": safe_name, "error": t("file.duplicate")})
            logger.info(f"アップロード: 重複のため無視: {safe_name}")
            continue

        ext = Path(safe_name).suffix.lower()
        if ext not in config.allowed_extensions:
            errors.append({"file": safe_name, "error": t("file.unsupported", ext=ext)})
            continue

        dest = INPUT_DIR / safe_name
        content = await file.read()
        if len(content) > 100 * 1024 * 1024:
            errors.append({"file": safe_name, "error": t("file.too_large")})
            continue

        with open(dest, "wb") as f:
            f.write(content)
        uploaded.append(safe_name)
        logger.info(f"アップロード: {safe_name} ({len(content)} bytes)")

    return {"status": "ok", "uploaded": uploaded, "errors": errors}


@app.get("/files")
async def list_files():
    files = []
    if INPUT_DIR.exists():
        done_names = get_done_filenames()
        for f in sorted(INPUT_DIR.iterdir()):
            # 処理済み（done.logに記録済み）のファイルはinput/に残っていても一覧に出さない
            if f.is_file() and f.name not in done_names:
                files.append(FileItem(
                    name=f.name,
                    size=f.stat().st_size,
                    status="pending",
                ))
    return {"files": files}


@app.delete("/files/{filename}")
async def delete_file(filename: str):
    safe_name = sanitize_filename(filename)
    if not is_safe_filename(safe_name):
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = INPUT_DIR / safe_name
    if target.exists():
        target.unlink()
        logger.info(f"ファイル削除: {safe_name}")
        return {"status": "ok", "deleted": safe_name}
    raise HTTPException(status_code=404, detail="File not found")


@app.delete("/files")
async def clear_files():
    count = 0
    if INPUT_DIR.exists():
        for f in INPUT_DIR.iterdir():
            if f.is_file():
                f.unlink()
                count += 1
    logger.info(f"全ファイル削除: {count}件")
    return {"status": "ok", "deleted_count": count}


@app.post("/process/cancel")
async def cancel_processing():
    """処理を中止"""
    global processing_status
    if processing_status["is_processing"]:
        processing_status["is_cancelled"] = True
        logger.info("処理中止リクエストを受け付け")
        return {"status": "ok", "cancelling": True, "message": t("proc.cancelling")}
    return {"status": "ok", "cancelling": False, "message": t("proc.not_running")}


def _resolve_process_settings(request: "ProcessRequest", config: AppConfig):
    """リクエストと設定からプロンプト・出力先を解決する"""
    # prompt_nameが指定された場合は、そのプロンプトの内容を使用
    if request.prompt_name and config.saved_prompts and request.prompt_name in config.saved_prompts:
        prompt_data = config.saved_prompts[request.prompt_name]
        if isinstance(prompt_data, dict):
            prompt = request.prompt or prompt_data.get("content", config.prompt)
        else:
            prompt = request.prompt or config.prompt
        prompt_name = request.prompt_name
    else:
        prompt = request.prompt or config.prompt
        prompt_name = config.active_prompt_name or t("prompt.default_label")

    # プロンプト設定を取得
    prompt_settings = config.saved_prompts.get(prompt_name, {})
    if not prompt_settings:
        # 指定プロンプトが見つからない場合はアクティブプロンプトの設定を使用
        prompt_settings = config.saved_prompts.get(config.active_prompt_name, {})
    output_dir_str = prompt_settings.get("output_dir", "")
    include_transcript = prompt_settings.get("include_transcript", False)

    # 出力先フォルダを決定 (.mdファイルの保存先)
    if output_dir_str and Path(output_dir_str).is_dir():
        output_dir = Path(output_dir_str)
    else:
        output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    return prompt, prompt_name, output_dir, include_transcript


# 処理の実行ごとに増える識別子。中止・完了後も取り残されたスレッド（AI生成・Python版Whisper）が、
# 次の処理の状態(processing_status)を書き換えたり、中止フラグのリセット後に生成を続けたりしないための印
_current_run_id = 0


class _ProcessCancelled(Exception):
    """ユーザーが「処理を中止」した（処理を打ち切って結果を保存しない）"""


def _raise_if_stale(run_id: int) -> None:
    """待機中に中止・終了した実行のスレッドが、後から認識を始めないようにする"""
    if run_id != _current_run_id or processing_status.get("is_cancelled"):
        raise _ProcessCancelled()


# 中止後も止められずに動き続けているブロッキング処理（Python版Whisper等）。残っている間は新しい処理を始めさせない
_lingering = set()


async def _await_cancellable(future, on_cancel=None, grace: float = 5.0):
    """executorで動くブロッキング処理の完了を待ちつつ、中止の要求を0.5秒ごとに確認する。

    中止されたら on_cancel()（例: vibe-serverを停止して認識のHTTPを打ち切る）を呼び、最大 grace 秒だけ
    処理の終了を待ってから _ProcessCancelled を送出する。完了と同時に中止が来た場合も中止を優先する。"""
    # 打ち切った後に例外で終わるスレッドの「未取得の例外」警告を出さない
    future.add_done_callback(lambda f: f.cancelled() or f.exception())
    while True:
        done, _ = await asyncio.wait({future}, timeout=0.5)
        if processing_status.get("is_cancelled"):
            if not done and on_cancel is not None:
                await on_cancel()
            if not done:
                if isinstance(future, asyncio.Future):
                    _lingering.add(future)  # 止められずに残った処理。終わるまで次の処理を始めさせない
                    future.add_done_callback(_lingering.discard)
                try:
                    await asyncio.wait_for(asyncio.shield(future), timeout=grace)
                except Exception:
                    pass  # 停止された処理が例外で終わる/猶予内に終わらない場合も、中止として扱う
            raise _ProcessCancelled()
        if done:
            return future.result()


def _start_processing_status(total_files: int):
    """処理開始時のprocessing_status初期化"""
    global _current_run_id
    _current_run_id += 1
    processing_status["is_processing"] = True
    processing_status["is_cancelled"] = False
    processing_status["current_file"] = None
    processing_status["progress"] = 0
    processing_status["total_files"] = total_files
    processing_status["completed_files"] = 0
    processing_status["message"] = t("proc.starting")
    processing_status["current_step"] = None
    processing_status["current_step_name"] = ""
    processing_status["step_progress"] = 0
    processing_status["current_output"] = ""
    processing_status["estimated_remaining"] = None
    processing_status["start_time"] = time.time()
    processing_status["audio_duration"] = None
    processing_status["step_started_at"] = None
    processing_status["recognition_started_at"] = None
    processing_status["recognition_predicted_sec"] = None


def _finish_processing_status():
    """処理完了時のprocessing_statusリセット"""
    global _current_run_id
    _current_run_id += 1  # 取り残されたスレッドから見て「この実行は終わった」と分かるようにする
    processing_status["is_processing"] = False
    processing_status["is_cancelled"] = False
    processing_status["current_file"] = None
    processing_status["progress"] = 100
    processing_status["message"] = t("proc.done")
    processing_status["current_step"] = None
    processing_status["current_step_name"] = ""
    processing_status["step_progress"] = 0
    processing_status["current_output"] = ""
    processing_status["audio_duration"] = None
    processing_status["estimated_remaining"] = None
    processing_status["step_started_at"] = None
    processing_status["recognition_started_at"] = None
    processing_status["recognition_predicted_sec"] = None


async def _generate_llm_report(transcript_text: str, filename: str, prompt: Optional[str], config: AppConfig) -> str:
    """LLM生成（ローカルLLM、llama-cpp-python固定）。
    テキスト入力パス・ファイル処理パスの両方から呼ばれる共通ロジック。"""
    loop = asyncio.get_event_loop()
    run_id = _current_run_id

    # ストリーミングコールバック: 生成中のテキストを更新（中止・終了した実行のスレッドは書き込まない）
    def on_llm_chunk(text):
        if run_id == _current_run_id:
            processing_status["current_output"] = text

    def check_cancelled():
        # 実行が終わった（IDが変わった）後も、取り残されたスレッドが生成を続けないよう止める
        return processing_status.get("is_cancelled", False) or run_id != _current_run_id

    # 「AIモデルの指定」で .gguf が指定されていればそれを使う（空=既定モデル）
    model_path = config.llm_model_path or None

    def local_gen():
        if not model_path:
            # 既定モデルは起動時に準備されるが、任意モデル指定で起動した後に既定へ戻した場合などは
            # 未取得のことがある。取得済みなら即戻る（未取得ならここでダウンロードする）
            local_llm.ensure_model_downloaded()
        # 実行装置は処理の途中で設定が変わることがあるため、毎回その時点の設定を使う
        llm_device = normalize_llm_device(load_config().llm_device)
        return local_llm.get_llamacpp_client(model_path, llm_device).generate_report_stream(
            transcript=transcript_text,
            filename=filename,
            on_chunk=on_llm_chunk,
            is_cancelled=check_cancelled,
            prompt=prompt,
        )

    # 本文はログに出さず、文字数・使用モデル・所要時間のみ記録する
    started = time.monotonic()
    logger.info(
        f"LLM生成開始: file={filename}, 入力={len(transcript_text)}文字, "
        f"モデル={model_path or DEFAULT_LLM_MODEL_LABEL}"
    )
    try:
        # モデル読み込み中・最初のチャンクが出る前も中止できるよう、完了待ちの間も中止を確認する
        result = await _await_cancellable(loop.run_in_executor(executor, local_gen))
    except _ProcessCancelled:
        logger.info(f"LLM生成を中止しました: file={filename} ({time.monotonic() - started:.1f}秒)")
        raise
    except Exception:
        logger.error(f"LLM生成に失敗: file={filename} ({time.monotonic() - started:.1f}秒)", exc_info=True)
        raise
    logger.info(f"LLM生成完了: file={filename}, 出力={len(result or '')}文字 ({time.monotonic() - started:.1f}秒)")
    return result


async def _process_text_input(request: "ProcessRequest", prompt: Optional[str], prompt_name: str, output_dir: Path, config: AppConfig) -> dict:
    """テキスト直接入力パスの処理。結果1件分のdictを返す"""
    now = time.localtime()
    date_prefix = f"{now.tm_year}-{now.tm_mon:02d}-{now.tm_mday:02d}_{now.tm_hour:02d}{now.tm_min:02d}"
    filename = f"{date_prefix}.md"
    processing_status["current_file"] = filename
    try:
        transcript_text = request.text_input

        # LLM 文章生成
        processing_status["current_step"] = "llm"
        processing_status["step_started_at"] = time.time()
        processing_status["current_step_name"] = t("step.ai_prompt", prompt=prompt_name)
        processing_status["message"] = t("step.text_processing")
        logger.info(f"[llm] {prompt_name}生成中: テキスト入力")

        elapsed = time.time() - processing_status.get("start_time", time.time())
        _update_progress_and_eta(PROCESS_STEPS_TEXT, "llm", 0, 0, 1, elapsed)

        report = await _generate_llm_report(transcript_text, filename, prompt, config)

        elapsed = time.time() - processing_status.get("start_time", time.time())
        _update_progress_and_eta(PROCESS_STEPS_TEXT, "llm", 100, 0, 1, elapsed)
        processing_status["current_output"] = ""

        processing_status["message"] = t("step.saving")
        # H1見出しがあれば「日時-タイトル」を保存名にする（無ければ日時のみ）。.mdと入力テキストの.txtは同じstem
        unique_stem = get_unique_stem(
            output_dir, build_output_stem(date_prefix, report, output_dir), (".md", ".txt"))
        result_file = output_dir / f"{unique_stem}.md"
        with open(result_file, "w", encoding="utf-8") as f:
            # テキスト手入力の場合は音声認識結果がないため、include_transcriptを無視
            f.write(f"{report}\n")

        # 入力したテキストも音声ファイルの文字起こしと同じく .txt として残す（履歴の削除で .md と一緒に消える）
        transcript_file = output_dir / f"{unique_stem}.txt"
        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write(transcript_text)

        processing_status["completed_files"] = 1

        return {
            "file": filename,
            "status": "ok",
            "transcript": transcript_text,
            "report": report,
            "result_file": str(result_file),
            "transcript_file": str(transcript_file),
        }

    except _ProcessCancelled:
        return None  # 中止: 途中までの文章は保存しない
    except Exception as e:
        logger.error(f"テキスト入力処理エラー: {e}", exc_info=True)
        return {"file": filename, "status": "error", "message": str(e)}


def _update_progress_and_eta(
    weighted_steps: list, step: Optional[str], step_progress: Optional[int],
    index: int, total_files: int, elapsed: float = 0.0,
) -> None:
    """`processing_status["progress"]`（進捗率）を更新する。

    推定時間(`estimated_remaining`)は、ここでは計算しない。AI生成は所要時間を予測できず、
    固定の重みで見積もると「あと10秒」と出たまま1〜2分かかるため、音声認識の推定と
    AI生成の経過時間を`/status`応答時に`eta.compute_eta_text()`で計算する。
    `elapsed`は使わない（呼び出し側の引数に合わせて受け取るだけ）。

    ファイル境界（index/total_files）だけでなく、ファイル内の現在ステップの
    位置（weighted_steps内での重み付き完了量 + ステップ内進捗）も加味することで、
    総ファイル数が1件のときに進捗率が0%に張り付いて推定時間が永久に
    計算されない不具合を解消する。各ステップは均等ではなく`weighted_steps`の
    重み（`_build_weighted_steps()`参照）に応じて進捗率に反映される。"""
    step_names = [name for name, _ in weighted_steps]
    if step not in step_names:
        step_fraction = 0
    else:
        total_weight = sum(weight for _, weight in weighted_steps)
        completed_weight = 0.0
        for name, weight in weighted_steps:
            if name == step:
                completed_weight += weight * ((step_progress or 0) / 100)
                break
            completed_weight += weight
        step_fraction = completed_weight / total_weight if total_weight > 0 else 0
    overall_progress = int(((index + step_fraction) / total_files) * 100)
    processing_status["progress"] = overall_progress


async def _process_one_file(
    filename: str, index: int, total_files: int,
    prompt: Optional[str], prompt_name: str,
    output_dir: Path, include_transcript: bool,
    whisper_model: str, config: AppConfig, results: list,
) -> bool:
    """ファイル一覧処理パスで1ファイル分を処理し、結果を`results`に直接追加する。
    元ファイルはinput/に残したままにし、doneフォルダへの移動は行わない。
    代わりにdone.logへ処理済みとして記録する（log_done_entry）。
    戻り値: should_break（Trueならファイルループを打ち切るべき。中止検知時のみTrue）"""
    safe_name = sanitize_filename(filename)
    if not is_safe_filename(safe_name):
        results.append({"file": filename, "status": "error", "message": t("file.invalid_name")})
        return False

    filepath = INPUT_DIR / safe_name
    if not filepath.exists():
        results.append({"file": safe_name, "status": "error", "message": t("file.not_found")})
        return False

    processing_status["current_file"] = filename
    processing_status["progress"] = int((index / total_files) * 100)
    # 前のファイルの時間表示（AI生成の経過時間・音声認識の推定）を引き継がない
    processing_status["step_started_at"] = None
    processing_status["recognition_started_at"] = None
    processing_status["recognition_predicted_sec"] = None

    short_name = Path(filename).stem
    counter = f" ({index + 1}/{total_files})"

    # テキストファイル（llmステップのみ）をデフォルトとし、音声/動画ファイルと判明した時点で
    # _build_weighted_steps()の結果に差し替える（progress_callbackはこの変数をクロージャで参照する）
    weighted_steps = PROCESS_STEPS_TEXT

    run_id = _current_run_id

    def progress_callback(step, message, progress=None):
        if run_id != _current_run_id:
            # 中止・終了済みの実行から遅れて呼ばれた通知は、次の処理の表示を汚さないよう捨てる。
            # 認識系の通知なら、取り残されたスレッドが次の処理用のvibe-serverへ送信しないよう打ち切る
            if step in ("ffmpeg", "model", "transcribe"):
                raise _ProcessCancelled()
            return
        # ステップ名のマッピング（ファイル名 + N/Mカウンタ付き）
        step_names = {
            "ffmpeg": t("step.convert", name=short_name, counter=counter),
            "model": t("step.model", name=short_name, counter=counter),
            "transcribe": t("step.transcribe", name=short_name, counter=counter),
            "llm": message or t("step.ai", name=short_name, counter=counter),
        }
        if processing_status.get("current_step") != step:
            processing_status["step_started_at"] = time.time()
        if step == "transcribe" and progress == 0:
            # 学習用: モデルの初回ダウンロード等を含めないため、文字起こし本体の開始時刻を記録する
            processing_status["transcribe_started_mono"] = time.monotonic()
        processing_status["current_step"] = step
        processing_status["current_step_name"] = step_names.get(step, f"{message}{counter}")
        # ファイル名を含むメッセージを設定
        processing_status["message"] = f"{filename} {message}"
        if progress is not None:
            processing_status["step_progress"] = progress

        elapsed = time.time() - processing_status.get("start_time", time.time())
        _update_progress_and_eta(weighted_steps, step, progress, index, total_files, elapsed)

    loop = asyncio.get_event_loop()
    file_started = time.monotonic()
    try:
        file_size = filepath.stat().st_size
    except OSError:
        file_size = -1
    logger.info(
        f"ファイル処理開始 ({index + 1}/{total_files}): {filename} ({file_size} bytes), プロンプト={prompt_name}"
    )

    try:
        # テキストファイルの場合は音声認識をスキップ
        if is_text_file(filepath):
            processing_status["current_step"] = "llm"
            processing_status["step_started_at"] = time.time()
            processing_status["current_step_name"] = t("step.read_text", name=short_name, counter=counter)
            processing_status["message"] = t("step.read_text_msg", name=filename)

            transcript_text = await loop.run_in_executor(
                executor,
                lambda: filepath.read_text(encoding="utf-8"),
            )
            logger.info(f"テキストファイル読み込み完了: {filename} ({len(transcript_text)}文字)")

            if processing_status["is_cancelled"]:
                logger.info("テキスト読み込み後: 処理が中止されました")
                return True

        else:
            # 1. FFmpeg音声抽出 + Whisper音声認識
            is_video = is_video_file(filepath)

            # 音声長を取得して表示（外部プロセス ffprobe/ffmpeg を起動するため、
            # イベントループを止めないよう別スレッドで行う）
            audio_dur = 0.0
            try:
                detected_dur = await asyncio.to_thread(get_audio_duration, filepath)
                if detected_dur > 0:
                    audio_dur = detected_dur
                    processing_status["audio_duration"] = detected_dur
            except Exception:
                pass

            # 実際に発生するステップ構成・重みを組み立てる（バックエンドにより`model`の
            # 有無が変わり、`transcribe`の重みは音声長に比例させる）
            weighted_steps = _build_weighted_steps(is_video, config.whisper_backend, audio_dur)

            # vibe-serverの遅延初期化（必要に応じて起動）
            await _await_cancellable(asyncio.ensure_future(ensure_vibe_ready()))

            # ensure_vibe_ready()がフォールバックした場合、それは別のconfigオブジェクトへの
            # 変更かつディスク保存のみなので、ここで保持しているconfig.whisper_backendには
            # 反映されていない。processing_status側のフラグで実際に使うバックエンドを決める。
            effective_whisper_backend = (
                "python" if processing_status.get("vibe_fallback_active") else config.whisper_backend
            )

            logger.info(
                f"音声認識開始: {filename} (backend={effective_whisper_backend}, "
                f"model={whisper_model}, 長さ={audio_dur:.1f}秒)"
            )
            # 音声認識の推定時間: 音声長 × 学習済み係数。表示は/status応答時に現在時刻から計算する
            factor = eta.load_factor(effective_whisper_backend)
            processing_status["recognition_predicted_sec"] = eta.predict_recognition_seconds(
                audio_dur, factor, is_video, effective_whisper_backend)
            processing_status["recognition_started_at"] = time.time()
            transcribe_started = time.monotonic()
            async def stop_recognition():
                # vibe-serverへのHTTPは途中で中断できないため、プロセスを止めて接続ごと打ち切る。
                # _vibe_ready を戻しておくと、次の処理の ensure_vibe_ready() が
                # 設定（モデル・パス）どおりに再起動する（約2秒）
                global _vibe_ready
                if effective_whisper_backend == "vibe":
                    logger.info("中止のため vibe-server を停止して音声認識を打ち切ります")
                    await loop.run_in_executor(startup_executor, vibe_client.stop_vibe)
                    _vibe_ready = False

            transcript_result = await _await_cancellable(
                loop.run_in_executor(
                    executor,
                    lambda: _raise_if_stale(run_id) or whisper_transcribe(
                        filepath,
                        model_name=whisper_model,
                        language=prompt_sets.recognition_language(config.whisper_language),
                        progress_callback=progress_callback,
                        backend=effective_whisper_backend,
                    ),
                ),
                on_cancel=stop_recognition,
            )
            recognition_elapsed = time.monotonic() - transcribe_started
            processing_status["recognition_started_at"] = None
            processing_status["recognition_predicted_sec"] = None
            # 学習には文字起こし本体の所要時間だけを使う（コールバックが無ければ認識全体で代用）
            mono = processing_status.pop("transcribe_started_mono", None)
            learn_elapsed = time.monotonic() - mono if mono is not None else recognition_elapsed
            eta.record_run(effective_whisper_backend, learn_elapsed, audio_dur, is_video,
                           transcribe_only=mono is not None)

            # 句読点・改行を補って読みやすくする（.txt・.mdへの埋め込み・LLM入力のすべてに同じ整形後テキストを使う）。
            # Pythonバックエンドは連結済みの1行テキストになるため、セグメント単位で改行したものを材料にする
            segments = transcript_result.get("segments") or []
            raw_text = (
                "\n".join(seg["text"].strip() for seg in segments if seg.get("text", "").strip())
                if segments else transcript_result["text"]
            )
            transcript_text = format_transcript(raw_text)
            logger.info(
                f"音声認識完了: {filename} ({len(transcript_text)}文字, "
                f"{recognition_elapsed:.1f}秒)"
            )

            # 中止チェック
            if processing_status["is_cancelled"]:
                logger.info("Whisper完了後: 処理が中止されました")
                return True

        # 2. LLM 文章生成
        progress_callback("llm", t("step.generating", prompt=prompt_name), 0)
        logger.info(f"[llm] {prompt_name}生成中: {filename}")

        report = await _generate_llm_report(transcript_text, filename, prompt, config)

        progress_callback("llm", t("step.generated", prompt=prompt_name), 100)
        processing_status["current_output"] = ""

        # 中止チェック
        if processing_status["is_cancelled"]:
            logger.info("LLM完了後: 処理が中止されました")
            return True

        # 3. 連番を付けて結果をファイルに保存
        processing_status["message"] = t("step.saving")
        # 保存名は「元ファイル名-H1タイトル」（H1が無ければ元ファイル名のみ）。.mdと.txtは同じstem
        unique_stem = get_unique_stem(
            output_dir, build_output_stem(Path(filename).stem, report, output_dir), (".md", ".txt"))
        result_file = output_dir / f"{unique_stem}.md"
        with open(result_file, "w", encoding="utf-8") as f:
            if include_transcript:
                f.write(f"{report}\n\n")
                f.write(f"## {t('out.transcript_heading')}\n\n{transcript_text}\n")
            else:
                f.write(f"{report}\n")

        # 生の音声認識結果を常に別ファイル(.txt)として保存する（include_transcriptの値に関わらず）。
        # ただしアップロードされたファイル自体がテキストファイルの場合、transcript_textは
        # 実際のASR結果ではなくユーザー自身の入力テキストそのものなので、無意味な複製を避けるため
        # 保存しない。
        transcript_file = None
        if not is_text_file(filepath):
            transcript_file = output_dir / f"{unique_stem}.txt"
            with open(transcript_file, "w", encoding="utf-8") as f:
                f.write(transcript_text)

        results.append({
            "file": filename,
            "status": "ok",
            "transcript": transcript_text,
            "report": report,
            "result_file": str(result_file),
            "transcript_file": str(transcript_file) if transcript_file else None,
        })

        # 4. done.logに処理済みとして記録する（元ファイルはinput/に残したまま移動しない）
        log_done_entry(filename)

        processing_status["completed_files"] = index + 1
        logger.info(
            f"ファイル処理完了: {filename} -> {result_file.name} "
            f"({time.monotonic() - file_started:.1f}秒)"
        )

        return False

    except _ProcessCancelled:
        logger.info(f"処理を中止しました: {filename}")
        return True
    except Exception as e:
        logger.error(f"処理エラー: {filename} - {e}", exc_info=True)
        results.append({"file": filename, "status": "error", "message": str(e)})
        return False


@app.post("/process")
async def process_files(request: ProcessRequest):
    global processing_status

    async with _process_lock:
        if processing_status["is_processing"]:
            raise HTTPException(status_code=409, detail=t("proc.busy"))
        if any(not f.done() for f in list(_lingering)):
            raise HTTPException(status_code=409, detail=t("proc.lingering"))

    config = load_config()
    prompt, prompt_name, output_dir, include_transcript = _resolve_process_settings(request, config)
    whisper_model = request.whisper_model or config.whisper_model
    # 同じ名前が重複して指定されても1回だけ処理する（順序は保つ）
    # （Windows は大文字小文字を区別しないので casefold を鍵にする）
    seen_names: set[str] = set()
    unique_files = []
    for name in request.files:
        if name.casefold() not in seen_names:
            seen_names.add(name.casefold())
            unique_files.append(name)
    if len(unique_files) != len(request.files):
        logger.info(f"重複するファイル指定を除外: {len(request.files) - len(unique_files)}件")
    request.files = unique_files
    total_files = max(len(request.files), 1 if request.text_input else 0)

    _start_processing_status(total_files)
    logger.info(
        f"処理リクエスト開始: ファイル={len(request.files)}件, "
        f"テキスト入力={'あり' if request.text_input else 'なし'}, プロンプト={prompt_name}"
    )

    results = []

    # テキスト直接入力の場合
    if request.text_input and not request.files:
        text_result = await _process_text_input(request, prompt, prompt_name, output_dir, config)
        if text_result is not None:  # None = 中止
            results.append(text_result)
        cancelled = processing_status["is_cancelled"] and processing_status.get("completed_files", 0) < total_files
        _finish_processing_status()

    else:
        for i, filename in enumerate(request.files):
            # 中止チェック
            if processing_status["is_cancelled"]:
                logger.info("処理が中止されました")
                break

            should_break = await _process_one_file(
                filename, i, total_files, prompt, prompt_name,
                output_dir, include_transcript, whisper_model, config, results,
            )
            if should_break:
                break

        # すべて処理し終えた後に届いた中止は「完了」として扱う
        cancelled = processing_status["is_cancelled"] and processing_status.get("completed_files", 0) < total_files
        _finish_processing_status()

    ok_count = sum(1 for r in results if r.get("status") == "ok")
    if cancelled:
        logger.info(f"処理リクエストを中止しました: 完了={ok_count}件")
        return ProcessResponse(
            status="cancelled",
            message=t("proc.cancelled"),
            results=results,
        )
    logger.info(f"処理リクエスト終了: 成功={ok_count}件, 失敗={len(results) - ok_count}件")

    return ProcessResponse(
        status="completed",
        message=t("proc.files_done", n=len(results)),
        results=results,
    )


def get_unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """連番を付けてユニークなファイルパスを取得"""
    base = directory / f"{stem}{suffix}"
    if not base.exists():
        return base
    counter = 1
    while True:
        new_path = directory / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


def get_unique_stem(directory: Path, stem: str, suffixes: tuple) -> str:
    """複数の拡張子をペアで保存する場合に、いずれの拡張子でも衝突しない共通stemを返す。
    get_unique_path()を拡張子ごとに別々に呼ぶと連番がずれる（例: meeting.mdは存在するが
    meeting.txtは存在しない場合、md側だけmeeting_1.mdになりtxt側はmeeting.txtのまま、
    というペアのズレが発生する）ため、両方の拡張子を同時にチェックして共通の連番を決める。"""
    candidate = stem
    counter = 0
    while any((directory / f"{candidate}{suf}").exists() for suf in suffixes):
        counter += 1
        candidate = f"{stem}_{counter}"
    return candidate


def _write_unique_file(directory: Path, stem: str, suffix: str, data: bytes) -> Path:
    """連番付きのユニークなパスにデータを書き込む。

    変換中のawaitを挟むと、同時に来た2つのダウンロード要求が同じ名前を選んで上書きし合うため、
    排他的作成(xb)で名前を確保し、既に存在すれば次の連番を試す。"""
    while True:
        path = get_unique_path(directory, stem, suffix)
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            continue


TEXT_EXTENSIONS = {".txt", ".md"}


def is_text_file(file_path: str | Path) -> bool:
    """ファイルがテキストファイルかどうかを判定"""
    return Path(file_path).suffix.lower() in TEXT_EXTENSIONS


# ==================== 処理履歴 API ====================

HISTORY_FILE = BASE_DIR / "history.json"


def load_history() -> dict:
    """履歴データを読み込み"""
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data
        except Exception:
            pass
    return {"hidden": []}


def is_file_hidden(file_id: str, hidden_list: list) -> bool:
    """ファイルが非表示リストに含まれているかチェック（タイムスタンプ比較対応）"""
    for item in hidden_list:
        if isinstance(item, dict):
            if item.get("id") == file_id:
                return True
        elif isinstance(item, str):
            if item == file_id:
                return True
    return False


def get_hidden_timestamp(file_id: str, hidden_list: list) -> float:
    """ファイルの非表示タイムスタンプを取得"""
    for item in hidden_list:
        if isinstance(item, dict):
            if item.get("id") == file_id:
                return item.get("hidden_at", 0.0)
        elif isinstance(item, str):
            if item == file_id:
                return 0.0
    return 0.0


def save_history(data: dict):
    """履歴データを保存"""
    HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_h1_title(report: str) -> Optional[str]:
    """レポート内容からタイトルを抽出 (H1 → H2 → H3 の順で取得)"""
    if not report:
        return None
    h2_candidate = None
    h3_candidate = None
    for line in report.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
        if stripped.startswith("## ") and not stripped.startswith("### ") and h2_candidate is None:
            h2_candidate = stripped[3:].strip()
        if stripped.startswith("### ") and h3_candidate is None:
            h3_candidate = stripped[4:].strip()
    return h2_candidate or h3_candidate


_FILENAME_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_title_for_filename(title: str, max_len: int = 40) -> str:
    """タイトル文字列をファイル名の一部として使える形に変換する（Windows予約文字・制御文字を除去）"""
    cleaned = _FILENAME_UNSAFE_CHARS.sub("_", title).strip().strip(".")
    # 履歴APIのis_safe_filename()が".."を含む名前を拒否するため、連続するドットは置換する
    cleaned = re.sub(r"\.{2,}", "_", cleaned)
    return cleaned[:max_len].rstrip(" .")


def extract_h1_only(report: str) -> Optional[str]:
    """レポート内容からH1見出し（`# `）のみを抽出する。

    extract_h1_title()はH2/H3にもフォールバックするが、ファイル名にはH1だけを使う
    （お客様面談記録などH1を出さないプロンプトの出力で、H2の小見出しが保存名になるのを避けるため）。"""
    if not report:
        return None
    for line in report.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip()
            return title or None
    return None


# Windowsのパス長上限(MAX_PATH=260)に対する余裕。拡張子(.txt)・連番(_NN)・終端分を見込む
_PATH_LEN_LIMIT = 250
_NAME_RESERVE = 8


def build_output_stem(original_stem: str, report: str, output_dir: Optional[Path] = None) -> str:
    """出力ファイル(.md/.txt)の保存名（拡張子なし）を作る。

    `<元ファイル名>-<H1タイトル>`（例: `V2026-10-29-AI活用伴走支援サービス「AIのすすめ」事業構想`）。
    H1が無い・使える文字が残らない場合は元ファイル名のみ。パス長が上限を超える場合はタイトルを切り詰める。"""
    title = extract_h1_only(report)
    safe_title = _sanitize_title_for_filename(title) if title else ""
    if not safe_title:
        return original_stem
    stem = f"{original_stem}-{safe_title}"
    if output_dir is not None:
        budget = _PATH_LEN_LIMIT - len(str(output_dir)) - 1 - _NAME_RESERVE
        if len(stem) > budget:
            room = budget - len(original_stem) - 1
            if room <= 0:
                return original_stem
            safe_title = safe_title[:room].rstrip(" .")
            if not safe_title:
                return original_stem
            stem = f"{original_stem}-{safe_title}"
    return stem


def get_scan_dirs(config: AppConfig) -> list:
    """スキャン対象ディレクトリの一覧を取得"""
    dirs = [OUTPUT_DIR]
    try:
        for prompt_name, prompt_data in config.saved_prompts.items():
            # 古い形式（文字列）と新しい形式（辞書）の両方に対応
            if isinstance(prompt_data, dict):
                custom_dir = prompt_data.get("output_dir", "")
            else:
                custom_dir = ""
            if custom_dir:
                custom_path = Path(custom_dir)
                if custom_path.is_dir() and custom_path not in dirs:
                    dirs.append(custom_path)
    except Exception as e:
        logger.warning(f"スキャンディレクトリ取得エラー: {e}")
    return dirs


def log_done_entry(filename: str) -> None:
    """処理済みファイル名をdone.logに記録する（ISO 8601形式のタイムスタンプ付き、1行1件で追記）。

    元ファイルはdoneフォルダへ移動せずinput/に残したままにする設計のため、
    「処理済みかどうか」はこのログの有無で判定する（get_done_filenames参照）。"""
    timestamp = datetime.now().isoformat(timespec="seconds")
    try:
        with open(DONE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{timestamp}\t{filename}\n")
    except Exception as e:
        logger.warning(f"done.logへの記録に失敗: {e}")


def get_done_filenames() -> set:
    """done.logに記録済みのファイル名一覧を取得する"""
    if not DONE_LOG_FILE.exists():
        return set()
    names = set()
    try:
        with open(DONE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                _, _, name = line.partition("\t")
                if name:
                    names.add(name)
    except Exception as e:
        logger.warning(f"done.logの読み込みに失敗: {e}")
    return names


class HistoryItem(BaseModel):
    id: str = ""  # "<保存先フォルダ>::<ファイル名>"。同名ファイルが別フォルダにあっても対象を特定できる（一括操作用）
    name: str
    size: int
    created: float
    report: Optional[str] = None
    h1_title: Optional[str] = None
    dir_path: str = ""


class HistoryIdsRequest(BaseModel):
    ids: list[str]


def _resolve_history_ids(ids: list[str], scan_dirs: list) -> list:
    """履歴のid("<フォルダ>::<ファイル名>")を (フォルダ, ファイル名) へ解決する。

    1つでも不正（区切り無し・走査対象外のフォルダ・.md以外・パストラバーサル）ならHTTP 400で全体を拒否する
    （一部だけ処理されて残りが処理されない状態を作らない）。ファイルが存在するかは確認しない。"""
    if not ids:
        raise HTTPException(status_code=400, detail=t("hist.no_selection"))
    allowed = {str(d): d for d in scan_dirs}
    resolved = []
    for file_id in ids:
        dir_part, sep, name = file_id.rpartition("::")
        name = sanitize_filename(name) if sep else ""
        if (not sep or dir_part not in allowed or not is_safe_filename(name) or not name.endswith(".md")
                or name != file_id.rpartition("::")[2]):
            raise HTTPException(status_code=400, detail="Invalid history id")
        resolved.append((allowed[dir_part], name, f"{dir_part}::{name}"))
    return resolved


@app.get("/history")
async def get_history(q: Optional[str] = None):
    """処理履歴一覧を取得（.mdファイルのみ、カスタム出力先含む）。

    レスポンスの各項目は本文（report）を含まない（一覧表示には不要なため、帯域節約のため省く。
    本文が必要な詳細表示は`GET /history/{filename}`で個別取得する）。
    `q`が指定された場合は、ファイル名・本文のいずれかに大文字小文字を無視した部分一致でサーバー側
    フィルタを行う（本文はH1タイトル抽出のためどのみ読み込むので、検索のためだけに
    追加のI/Oは発生しない）。H1タイトルは本文の1行目に含まれるため、タイトルへの一致は本文一致に
    包含される。よって個別のタイトル一致判定は行わない"""
    config = load_config()
    history = load_history()
    hidden_list = history.get("hidden", [])
    scan_dirs = get_scan_dirs(config)
    query = q.lower() if q else None
    items = []

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for f in scan_dir.iterdir():
            if not f.is_file() or f.suffix != ".md":
                continue
            file_id = f"{scan_dir}::{f.name}"
            # 非表示チェック: タイムスタンプ比較で新しいファイルは表示
            if is_file_hidden(file_id, hidden_list):
                hidden_at = get_hidden_timestamp(file_id, hidden_list)
                file_mtime = f.stat().st_mtime
                # ファイルの更新日時が非表示時より新しい場合 → 表示
                if hidden_at > 0 and file_mtime > hidden_at:
                    pass  # 表示する
                else:
                    continue
            report = None
            try:
                report = f.read_text(encoding="utf-8")
            except Exception:
                pass
            h1_title = extract_h1_title(report)

            if query:
                name_match = query in f.name.lower()
                report_match = query in (report or "").lower()
                if not (name_match or report_match):
                    continue

            items.append(HistoryItem(
                id=file_id,
                name=f.name,
                size=f.stat().st_size,
                created=f.stat().st_mtime,
                report=None,
                h1_title=h1_title,
                dir_path=str(scan_dir),
            ))

    items.sort(key=lambda x: x.created, reverse=True)
    return {"items": items}


@app.get("/history/{filename}")
async def get_history_detail(filename: str):
    """履歴ファイル1件の本文を取得（詳細モーダル表示用）"""
    filename = sanitize_filename(filename)
    if not is_safe_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    config = load_config()
    scan_dirs = get_scan_dirs(config)

    for scan_dir in scan_dirs:
        target = scan_dir / filename
        if target.exists() and target.suffix == ".md":
            try:
                report = target.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"履歴ファイル読み込みエラー: {e}")
                raise HTTPException(status_code=500, detail=t("file.read_failed"))
            return HistoryItem(
                id=f"{scan_dir}::{target.name}",
                name=target.name,
                size=target.stat().st_size,
                created=target.stat().st_mtime,
                report=report,
                h1_title=extract_h1_title(report),
                dir_path=str(scan_dir),
            )

    raise HTTPException(status_code=404, detail="File not found")


@app.post("/history/hide")
async def hide_history_bulk(request: HistoryIdsRequest):
    """チェックで選んだ複数件を履歴から非表示にする（ファイルは削除しない）"""
    resolved = _resolve_history_ids(request.ids, get_scan_dirs(load_config()))
    history = load_history()
    hidden = skipped = 0
    now = time.time()
    for scan_dir, name, file_id in resolved:
        target = scan_dir / name
        if not target.exists():
            skipped += 1
            continue
        if is_file_hidden(file_id, history["hidden"]):
            # 非表示後に更新されて再表示されているファイルは、記録を新しくして改めて隠す
            if target.stat().st_mtime <= get_hidden_timestamp(file_id, history["hidden"]):
                skipped += 1
                continue
            history["hidden"] = [h for h in history["hidden"]
                                 if not (isinstance(h, dict) and h.get("id") == file_id) and h != file_id]
        history["hidden"].append({"id": file_id, "hidden_at": now})
        hidden += 1
    if hidden:
        save_history(history)
    logger.info(f"履歴を一括で非表示: {hidden}件（スキップ{skipped}件）")
    return {"status": "ok", "hidden_count": hidden, "skipped": skipped}


@app.post("/history/delete-files")
async def delete_history_files_bulk(request: HistoryIdsRequest):
    """チェックで選んだ複数件の結果ファイル(.md)と、同名の文字起こし(.txt)を削除する（元に戻せない）"""
    resolved = _resolve_history_ids(request.ids, get_scan_dirs(load_config()))
    history = load_history()
    deleted = txt_deleted = skipped = 0
    ids_removed = set()
    for scan_dir, name, file_id in resolved:
        target = scan_dir / name
        if not target.is_file():
            skipped += 1
            continue
        target.unlink()
        deleted += 1
        txt = target.with_suffix(".txt")
        if txt.is_file():
            txt.unlink()
            txt_deleted += 1
        ids_removed.add(file_id)
    if ids_removed:
        # 同名の新しいファイルが後で作られたときに、古い非表示の記録で隠れないよう掃除する
        history["hidden"] = [h for h in history["hidden"]
                             if not (isinstance(h, dict) and h.get("id") in ids_removed)]
        save_history(history)
    logger.info(f"履歴のファイルを一括削除: {deleted}件（.txt {txt_deleted}件、スキップ{skipped}件）")
    return {"status": "ok", "deleted_count": deleted, "txt_deleted_count": txt_deleted, "skipped": skipped}


@app.delete("/history/{filename}")
async def hide_history(filename: str):
    """履歴から非表示にする（ファイルは削除しない）"""
    filename = sanitize_filename(filename)
    if not is_safe_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    config = load_config()
    scan_dirs = get_scan_dirs(config)
    history = load_history()

    for scan_dir in scan_dirs:
        target = scan_dir / filename
        if target.exists() and target.suffix == ".md":
            file_id = f"{scan_dir}::{filename}"
            # 既に非表示の場合はスキップ
            if is_file_hidden(file_id, history["hidden"]):
                return {"status": "ok", "hidden": filename}
            # タイムスタンプ付きで非表示に追加
            history["hidden"].append({
                "id": file_id,
                "hidden_at": time.time()
            })
            save_history(history)
            logger.info(f"履歴非表示: {filename} (dir: {scan_dir})")
            return {"status": "ok", "hidden": filename}

    raise HTTPException(status_code=404, detail="File not found")


@app.delete("/history/{filename}/file")
async def delete_history_file(filename: str):
    """履歴ファイルを物理削除"""
    filename = sanitize_filename(filename)
    if not is_safe_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    config = load_config()
    scan_dirs = get_scan_dirs(config)

    for scan_dir in scan_dirs:
        target = scan_dir / filename
        if target.exists() and target.suffix == ".md":
            target.unlink()
            txt = target.with_suffix(".txt")
            if txt.is_file():
                txt.unlink()
            logger.info(f"履歴ファイル削除: {filename} (dir: {scan_dir})")
            return {"status": "ok", "deleted": filename}

    raise HTTPException(status_code=404, detail="File not found")


@app.post("/history/{filename}/download")
async def download_history_file(filename: str, format: str = "md"):
    """履歴ファイルを指定形式(txt/docx/pdf/md)に変換してDownloadsフォルダに保存する。

    履歴の実体は常に.md。`md`はそのままコピーし、それ以外はMarkdownを変換して生成する。
    `format`省略時は`md`。"""
    import shutil
    filename = sanitize_filename(filename)
    if not is_safe_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if format not in DOWNLOAD_FORMATS:
        raise HTTPException(status_code=400, detail=f"Invalid format: {format}")

    config = load_config()
    scan_dirs = get_scan_dirs(config)

    for scan_dir in scan_dirs:
        target = scan_dir / filename
        if target.exists() and target.suffix == ".md":
            downloads_dir = Path.home() / "Downloads"
            downloads_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(filename).stem
            # ユーザーが自分で作った.mdにBOMが付いていても見出しとして解釈できるよう utf-8-sig で読む
            markdown = target.read_text(encoding="utf-8-sig")
            title = extract_h1_title(markdown)
            if title:
                safe_title = _sanitize_title_for_filename(title)
                # 保存名にタイトルが既に含まれている場合は二重に付けない
                if safe_title and not re.sub(r"_\d+$", "", stem).endswith(f"-{safe_title}"):
                    stem = f"{stem}_{safe_title}"

            if format == "md":
                dest = get_unique_path(downloads_dir, stem, ".md")
                shutil.copy2(str(target), str(dest))
            else:
                convert = {
                    "txt": output_formats.markdown_to_txt,
                    "docx": output_formats.markdown_to_docx,
                    "pdf": output_formats.markdown_to_pdf,
                }[format]
                try:
                    data = await asyncio.get_event_loop().run_in_executor(executor, convert, markdown)
                except output_formats.OutputFormatError as e:
                    logger.error(f"履歴ファイル変換エラー ({format}): {e}")
                    raise HTTPException(status_code=500, detail=str(e))
                except Exception as e:
                    logger.error(f"履歴ファイル変換エラー ({format}): {e}", exc_info=True)
                    raise HTTPException(status_code=500, detail=t("conv.failed", format=format, error=e))
                dest = _write_unique_file(downloads_dir, stem, f".{format}", data)
            logger.info(f"履歴ファイルダウンロード: {filename} ({format}) -> {dest}")
            return {"status": "ok", "downloaded": str(dest), "format": format}

    raise HTTPException(status_code=404, detail="File not found")


@app.delete("/history")
async def clear_history():
    """全履歴を非表示にする（ファイルは削除しない）"""
    config = load_config()
    scan_dirs = get_scan_dirs(config)
    history = load_history()

    count = 0
    now = time.time()
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for f in scan_dir.iterdir():
            if f.is_file() and f.suffix == ".md":
                file_id = f"{scan_dir}::{f.name}"
                if not is_file_hidden(file_id, history["hidden"]):
                    history["hidden"].append({
                        "id": file_id,
                        "hidden_at": now
                    })
                    count += 1

    save_history(history)
    logger.info(f"全履歴非表示: {count}件")
    return {"status": "ok", "hidden_count": count}


# ==================== 設定 API ====================

@app.get("/config")
async def get_config():
    config = load_config()
    return {
        "whisper_backend": config.whisper_backend,
        "whisper_model": config.whisper_model,
        "whisper_language": config.whisper_language,
        "prompt": config.prompt,
        "dark_mode": config.dark_mode,
        "ui_language": config.ui_language,
        "builtin_prompt_names": list(prompt_sets.builtin_names()),
        # config.jsonを手編集して不正値が入っていても、フロントエンドには有効な形式を返す
        "download_format": config.download_format if config.download_format in DOWNLOAD_FORMATS else DOWNLOAD_FORMATS[0],
        "download_formats": list(DOWNLOAD_FORMATS),
        "max_files": config.max_files,
        "max_total_size_bytes": config.max_total_size_bytes,
        "allowed_extensions": list(config.allowed_extensions),
        "active_prompt_name": config.active_prompt_name,
        "saved_prompts": config.saved_prompts or {},
        "output_dir": str(OUTPUT_DIR),
        "vibe_server_path": config.vibe_server_path,
        "vibe_model": config.vibe_model,
        "llm_model_path": config.llm_model_path,
        "llm_model_default_label": DEFAULT_LLM_MODEL_LABEL,
        "llm_device": normalize_llm_device(config.llm_device),
        "vibe_running": vibe_client.is_vibe_running(),
        "vibe_port": vibe_client.get_vibe_port(),
    }


@app.post("/config")
async def update_config(request: ConfigUpdateRequest):
    changed = sorted(k for k, v in request.model_dump().items() if v is not None)
    # プロンプト本文などのユーザーコンテンツはログに出さず、更新されたキー名のみ記録する
    logger.info(f"設定更新リクエスト: 項目={changed}")
    config = load_config()

    # 検証エラー時は他の項目も保存しない（先に検証する）
    new_llm_model_path = None
    if request.llm_model_path is not None:
        try:
            new_llm_model_path = normalize_llm_model_path(request.llm_model_path)
        except ValueError as e:
            logger.warning(f"AIモデルの指定が不正です: {e}")
            return {"status": "error", "message": str(e)}

    if request.vibe_model is not None and not is_valid_vibe_model(request.vibe_model):
        raise HTTPException(status_code=400, detail=t("cfg.invalid_vibe_model", value=request.vibe_model))
    if request.llm_device is not None and request.llm_device not in LLM_DEVICES:
        raise HTTPException(status_code=400, detail=t("cfg.invalid_llm_device", value=request.llm_device))

    if request.whisper_backend is not None:
        config.whisper_backend = request.whisper_backend
        logger.info(f"音声認識バックエンドを更新: {request.whisper_backend}")
    if request.whisper_model is not None:
        config.whisper_model = request.whisper_model
        logger.info(f"Whisperモデルを更新: {request.whisper_model}")
    if request.whisper_language is not None:
        config.whisper_language = request.whisper_language
    if request.prompt is not None:
        config.prompt = request.prompt
        logger.info("プロンプトを更新")
    if request.dark_mode is not None:
        config.dark_mode = request.dark_mode
        logger.info(f"ダークモードを更新: {request.dark_mode}")
    if request.ui_language is not None:
        if request.ui_language not in i18n.SUPPORTED_LANGUAGES:
            raise HTTPException(status_code=400, detail=t("cfg.invalid_language", value=request.ui_language))
        # 編集していない初期プロンプトと既定の音声認識言語も、新しい言語のものへ入れ替わる
        prompt_sets.switch_language(config, request.ui_language)
        logger.info(f"表示言語を更新: {request.ui_language}")
    if request.download_format is not None:
        if request.download_format not in DOWNLOAD_FORMATS:
            raise HTTPException(status_code=400, detail=f"Invalid download_format: {request.download_format}")
        config.download_format = request.download_format
        logger.info(f"ダウンロード形式の既定値を更新: {request.download_format}")
    if request.active_prompt_name is not None:
        config.active_prompt_name = request.active_prompt_name
        # 選択されたプロンプトの内容をpromptに反映
        if config.saved_prompts and request.active_prompt_name in config.saved_prompts:
            prompt_data = config.saved_prompts[request.active_prompt_name]
            if isinstance(prompt_data, dict):
                config.prompt = prompt_data.get("content", "")
            else:
                config.prompt = prompt_data
        logger.info(f"アクティブプロンプトを更新: {request.active_prompt_name}")
    if request.vibe_server_path is not None:
        config.vibe_server_path = request.vibe_server_path
        logger.info(f"vibe-server.exeパスを更新: {request.vibe_server_path}")
    vibe_model_changed = False
    if request.vibe_model is not None:
        vibe_model_changed = request.vibe_model != config.vibe_model
        config.vibe_model = request.vibe_model
        logger.info(f"文字起こしモデルを更新: {request.vibe_model}")
    if new_llm_model_path is not None:
        config.llm_model_path = new_llm_model_path
        logger.info(f"AIモデルを更新: {new_llm_model_path or DEFAULT_LLM_MODEL_LABEL}")
    device_changed = False
    if request.llm_device is not None:
        device_changed = normalize_llm_device(config.llm_device) != request.llm_device
        config.llm_device = request.llm_device
        logger.info(f"LLM の実行装置を更新: {request.llm_device}")

    try:
        save_config(config)
        logger.info("設定を保存しました")
        if vibe_model_changed:
            _apply_vibe_model_change()
        if device_changed:
            # 再起動なしで反映するため、モデルを新しい装置で読み込み直す（バックグラウンド）
            asyncio.get_event_loop().run_in_executor(
                startup_executor, _reload_llm_for_device, config.llm_model_path or None, config.llm_device)
        return {"status": "ok", "message": t("cfg.saved")}
    except Exception as e:
        logger.error(f"設定保存エラー: {e}")
        return {"status": "error", "message": t("cfg.save_failed", error=e)}


class VibeModelStatusResponse(BaseModel):
    model: str
    status: str  # ready（取得済み）/ downloading（ダウンロード中・未完成）/ missing（未取得）
    app_downloading: bool = False  # このアプリがダウンロードを実行中か
    size_mb: Optional[int] = None  # おおよその大きさ（MB）。不明なら null


class VibeModelDownloadRequest(BaseModel):
    model: str


def _vibe_model_status(model: str) -> VibeModelStatusResponse:
    name = normalize_model_name(model)
    task = _vibe_model_download_tasks.get(name)
    app_downloading = task is not None and not task.done()
    status = vibe_client.model_status(name, vibe_client.find_vibe_binary_dir())
    size = vibe_client._MODEL_EXPECTED_BYTES.get(name)
    return VibeModelStatusResponse(
        model=name, status=str(status), app_downloading=app_downloading,
        size_mb=int(round(size / 1_000_000)) if isinstance(size, int) else None)


@app.get("/vibe/model/status", response_model=VibeModelStatusResponse)
async def vibe_model_status(model: str):
    """文字起こしモデルがこの PC にあるか（設定の保存時に、ダウンロードの確認を出すかの判断に使う）"""
    if not is_valid_vibe_model(model):
        raise HTTPException(status_code=400, detail=t("cfg.invalid_vibe_model", value=model))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(startup_executor, _vibe_model_status, model)


@app.post("/vibe/model/download", response_model=VibeModelStatusResponse)
async def vibe_model_download(request: VibeModelDownloadRequest):
    """利用者が選んだ文字起こしモデルのダウンロードを始める（バックグラウンド。進捗は /status）。

    取得済みなら何もしない"""
    name = normalize_model_name(request.model)
    if not is_valid_vibe_model(name) or name not in vibe_client._MODEL_EXPECTED_BYTES:
        raise HTTPException(status_code=400, detail=t("cfg.invalid_vibe_model", value=request.model))
    loop = asyncio.get_event_loop()
    current = await loop.run_in_executor(startup_executor, _vibe_model_status, name)
    if current.status != "ready":
        _start_vibe_model_download(name)
        current = await loop.run_in_executor(startup_executor, _vibe_model_status, name)
    return current


def _apply_vibe_model_change() -> None:
    """設定で文字起こしモデルを変えたとき、再起動なしで新しいモデルを使うようにする。

    vibe-server はモデルを起動時にしか指定できないため、「準備済み」の印を戻す。次の準備（ensure_vibe_ready）で
    未取得のモデルをダウンロードし、新しいモデルで vibe-server を起動し直す（vibe_client.start_vibe が稼働中の
    サーバーのモデルとの違いを検出して再起動する）。処理中は止めないよう、その処理が終わった次の処理で反映する。
    処理中でなければ、すぐ裏で準備を始める。"""
    global _vibe_ready, _vibe_generation
    _vibe_generation += 1
    _vibe_ready = False
    if processing_status.get("is_processing"):
        logger.info("処理中のため、文字起こしモデルの変更は次の処理から反映します")
        return

    async def prepare():
        try:
            await ensure_vibe_ready()
        except Exception as e:  # noqa: BLE001 - 失敗しても次の処理の準備で再試行する
            logger.warning(f"文字起こしモデル変更後の準備に失敗しました（次の処理で再試行します）: {e}")

    asyncio.get_event_loop().create_task(prepare())


def _reload_llm_for_device(model_path, device) -> None:
    """実行装置の変更を反映するため、LLM を読み込み直す（失敗しても設定の保存は有効）"""
    try:
        local_llm.preload_model(model_path, device)
    except Exception as e:
        logger.warning(f"LLM の読み込み直しに失敗しました（次回の利用時に再試行します）: {e}")


class LlmStatusResponse(BaseModel):
    configured: str  # 設定値: auto / gpu / cpu
    device: Optional[str] = None  # 実際に使っている装置: "gpu" / "cpu"（モデル読み込み前は None）
    device_name: str = ""  # GPU 名（取得できた場合）
    reason: str = ""  # CPU になった理由（cpu_selected / no_gpu / previous_crash / gpu_load_failed）
    gpu_available: bool = False  # この PC で GPU が使える（Vulkan 版でオフロード対応）か
    gpu_disabled_by_crash: bool = False  # 前回 GPU で強制終了したため自動で CPU にしているか


@app.get("/llm/status", response_model=LlmStatusResponse)
async def llm_status():
    """LLM の実行装置の設定と、実際に使っている装置を返す"""
    config = load_config()
    # 初回は llama_cpp の読み込み（Vulkan の DLL）で時間がかかることがあるため、イベントループを止めない
    loop = asyncio.get_event_loop()
    status = await loop.run_in_executor(startup_executor, local_llm.get_llm_device_status, config.llm_device)
    return LlmStatusResponse(**status)


@app.post("/config/reset")
async def reset_config_endpoint(request: ConfigResetRequest):
    """設定をデフォルトに戻す"""
    logger.info(f"設定リセットリクエスト: tab={request.tab}")
    try:
        old_config = load_config()
        old_device = normalize_llm_device(old_config.llm_device)
        old_vibe_model = old_config.vibe_model
        config = reset_config(request.tab)
        if config.vibe_model != old_vibe_model:
            _apply_vibe_model_change()
        if normalize_llm_device(config.llm_device) != old_device:
            asyncio.get_event_loop().run_in_executor(
                startup_executor, _reload_llm_for_device, config.llm_model_path or None, config.llm_device)
        return {"status": "ok", "message": t("cfg.reset")}
    except Exception as e:
        logger.error(f"設定リセットエラー: {e}")
        return {"status": "error", "message": t("cfg.reset_failed", error=e)}


# ==================== プロンプト管理 API ====================

@app.get("/prompts")
async def list_prompts():
    """保存済みプロンプト一覧を取得"""
    config = load_config()
    return {
        "prompts": config.saved_prompts or {},
        "active_name": config.active_prompt_name,
    }


@app.post("/prompts")
async def save_prompt(request: PromptRequest):
    """プロンプトを保存（新規・上書き）"""
    config = load_config()
    if config.saved_prompts is None:
        config.saved_prompts = {}

    # 既存プロンプトを取得（あれば）
    existing = config.saved_prompts.get(request.name, {})
    if isinstance(existing, str):
        existing = {"content": existing, "description": "", "output_dir": "", "include_transcript": False,
                    "hidden": False}

    config.saved_prompts[request.name] = {
        "content": request.content if request.content is not None else existing.get("content", ""),
        "description": request.description if request.description is not None else existing.get("description", ""),
        "output_dir": request.output_dir if request.output_dir is not None else existing.get("output_dir", ""),
        "include_transcript": request.include_transcript if request.include_transcript is not None else existing.get("include_transcript", False),
        "hidden": request.hidden if request.hidden is not None else bool(existing.get("hidden", False)),
    }

    save_config(config)
    logger.info(f"プロンプト保存: {request.name}")
    return {"status": "ok", "message": t("prompt.saved", name=request.name)}


@app.delete("/prompts/{name}")
async def delete_prompt(name: str):
    """プロンプトを削除"""
    config = load_config()
    if not config.saved_prompts or name not in config.saved_prompts:
        return {"status": "error", "message": t("prompt.not_found", name=name)}
    if name in prompt_sets.builtin_names():
        return {"status": "error", "message": t("prompt.builtin_undeletable")}
    del config.saved_prompts[name]
    # 削除されたプロンプトがアクティブだった場合、既定のプロンプト（文章校正）に戻す
    if config.active_prompt_name == name:
        fallback = prompt_sets.default_active_prompt_name(config.ui_language)
        config.active_prompt_name = fallback
        default_prompt = config.saved_prompts.get(fallback, {})
        if isinstance(default_prompt, dict):
            config.prompt = default_prompt.get("content", "")
        else:
            config.prompt = default_prompt
    save_config(config)
    logger.info(f"プロンプト削除: {name}")
    return {"status": "ok", "message": t("prompt.deleted", name=name)}


def _write_prompt_backup() -> Path:
    """保存済みプロンプトすべてを「ダウンロード」フォルダへ書き出す（ブロッキング）"""
    config = load_config()
    downloads_dir = Path.home() / "Downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    data = prompt_backup.build_backup(config.saved_prompts, APP_VERSION)
    return _write_unique_file(downloads_dir, prompt_backup.backup_filename(), ".json", data)


@app.post("/prompts/backup")
async def backup_prompts():
    """保存済みプロンプトをすべて ~/Downloads に JSON でバックアップする"""
    try:
        dest = await asyncio.get_event_loop().run_in_executor(executor, _write_prompt_backup)
    except OSError as e:
        logger.error(f"プロンプトのバックアップに失敗: {e}")
        return {"status": "error", "message": t("backup.write_failed", error=e)}
    logger.info(f"プロンプトをバックアップ: {dest}")
    return {"status": "ok", "path": str(dest), "message": t("backup.saved", path=dest)}


def _read_prompt_backup(request: PromptRestoreRequest) -> dict:
    """復元要求からバックアップの内容を読み、検証済みのプロンプトを返す（ブロッキング）"""
    if request.content is not None:
        return prompt_backup.parse_backup(request.content)
    path = Path((request.path or "").strip().strip('"'))
    if not request.path or not path.is_file():
        raise prompt_backup.PromptBackupError("backup.file_not_found")
    if path.stat().st_size > prompt_backup.MAX_BACKUP_BYTES:
        raise prompt_backup.PromptBackupError("backup.too_large", size=prompt_backup.MAX_BACKUP_BYTES // (1024 * 1024))
    return prompt_backup.parse_backup(path.read_bytes())


async def _load_restore_source(request: PromptRestoreRequest):
    """(プロンプト, エラー応答) を返す。読めなかったときはプロンプトが None"""
    try:
        prompts = await asyncio.get_event_loop().run_in_executor(executor, _read_prompt_backup, request)
        return prompts, None
    except prompt_backup.PromptBackupError as e:
        logger.warning(f"プロンプトのバックアップを読めません: {e.key}")
        return None, {"status": "error", "message": t(e.key, **e.values)}
    except OSError as e:
        logger.error(f"プロンプトのバックアップの読み込みに失敗: {e}")
        return None, {"status": "error", "message": t("backup.read_failed", error=e)}


@app.post("/prompts/restore/preview")
async def preview_prompt_restore(request: PromptRestoreRequest):
    """復元の前に、バックアップの件数・上書きされる名前・置き換えでも残る初期プロンプトを返す"""
    prompts, error = await _load_restore_source(request)
    if error:
        return error
    return {"status": "ok", **prompt_backup.preview(load_config().saved_prompts, prompts)}


@app.post("/prompts/restore")
async def restore_prompts(request: PromptRestoreRequest):
    """バックアップから保存済みプロンプトを復元する（merge: 追加＋同名は上書き / replace: 全て置き換え）"""
    if request.mode not in prompt_backup.RESTORE_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {request.mode}")
    prompts, error = await _load_restore_source(request)
    if error:
        return error
    config = load_config()
    prompt_backup.apply_restore(config, prompts, request.mode)
    save_config(config)
    logger.info(f"プロンプトを復元: {len(prompts)}件 (mode={request.mode})")
    return {"status": "ok", "count": len(prompts), "active_name": config.active_prompt_name,
            "message": t("backup.restored", n=len(prompts))}


@app.get("/model/info")
async def model_info():
    return {
        "whisper": get_model_info(),
        "available_models": AVAILABLE_MODELS,
    }


@app.post("/open-output-folder")
async def open_output_folder():
    """outputフォルダをエクスプローラーで開く"""
    import subprocess
    import platform
    try:
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", str(OUTPUT_DIR)])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(OUTPUT_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT_DIR)])
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"フォルダを開けませんでした: {e}")
        return {"status": "error", "message": str(e)}


# 設定の「情報」タブ・ウィザードのリンクで開いてよい外部サイト（https のみ。それ以外のURLは開かない）
OPEN_URL_ALLOWED_HOSTS = ("github.com", "www.8asue.com", "www.youtube.com")


class OpenUrlRequest(BaseModel):
    url: str


def _is_allowed_external_url(url: str) -> bool:
    """https で、許可したホスト（ユーザー情報・ポート指定なし）のURLか"""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname in OPEN_URL_ALLOWED_HOSTS
            and parts.netloc == parts.hostname and not any(c.isspace() for c in url))


@app.post("/open-url")
async def open_url(request: OpenUrlRequest):
    """外部リンク（GitHub・福栄ラボ・YouTube・Vibe の配布ページ）を既定のブラウザで開く"""
    if not _is_allowed_external_url(request.url):
        raise HTTPException(status_code=400, detail=t("open_url.not_allowed"))
    import webbrowser

    webbrowser.open(request.url)
    return {"status": "ok"}


# ファイル/フォルダ選択ダイアログの種類 → (フォルダ選択か, ファイルの種類フィルタ)
_DIALOG_SPECS = {
    # 説明文はpywebviewの検査(英数字・日本語・空白のみ。ハイフン等は不可)に通る形にする
    "exe": (False, ("vibe server (*.exe)", "dlg.all_files")),
    "gguf": (False, ("dlg.gguf", "dlg.all_files")),
    "folder": (True, ()),
    "prompts_json": (False, ("dlg.prompts_json", "dlg.all_files")),
}


def _dialog_file_types(kind: str) -> tuple:
    """ダイアログのファイルの種類フィルタ。説明文は表示言語に合わせる（"dlg." で始まるものは辞書のキー）"""
    return tuple(t(x) if x.startswith("dlg.") else x for x in _DIALOG_SPECS[kind][1])


def _dialog_initial_dir(initial: Optional[str]) -> str:
    """入力欄の値からダイアログの初期フォルダを決める。実在しなければ空（OS既定）"""
    text = (initial or "").strip().strip('"\'')
    if not text:
        return ""
    try:
        path = Path(text)
        if not path.is_absolute():
            return ""  # 「gemma4:e2b-qat(llama.cpp)」等の既定表示や相対パスは初期位置に使わない
        if path.is_dir():
            return str(path)
        if path.parent.is_dir() and path.suffix:
            return str(path.parent)
    except OSError:
        pass
    return ""


def _run_pick_dialog(kind: str, initial: Optional[str]) -> dict:
    """アプリのウィンドウ(pywebview)でOSのファイル選択ダイアログを開く（ブロッキング）"""
    import webview

    windows = webview.windows
    if not windows:
        return {"status": "error", "message": t("dlg.cannot_open")}
    is_folder = _DIALOG_SPECS[kind][0]
    file_types = _dialog_file_types(kind)
    result = windows[0].create_file_dialog(
        webview.FileDialog.FOLDER if is_folder else webview.FileDialog.OPEN,
        directory=_dialog_initial_dir(initial),
        allow_multiple=False,
        file_types=file_types,
    )
    if not result:
        return {"status": "cancel"}
    return {"status": "ok", "path": str(result[0])}


@app.post("/dialog/pick")
async def pick_path(request: DialogPickRequest):
    """設定画面の「選択」ボタン用: ファイル/フォルダ選択ダイアログを開き、選ばれたパスを返す。

    ダイアログはアプリのウィンドウ側(pywebview)で開く。ウィンドウが無い場合（ブラウザ単体で開いた等）は
    errorを返し、画面側は手入力にフォールバックする。"""
    if request.kind not in _DIALOG_SPECS:
        raise HTTPException(status_code=400, detail=f"Invalid kind: {request.kind}")
    initial = request.initial
    if request.kind == "prompts_json" and not initial:
        initial = str(Path.home() / "Downloads")  # バックアップの保存先から選べるようにする
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(dialog_executor, _run_pick_dialog, request.kind, initial)
    except Exception as e:
        logger.error(f"選択ダイアログのエラー: {e}", exc_info=True)
        return {"status": "error", "message": t("dlg.cannot_open")}


# ==================== ウィザード API ====================

class WizardSaveRequest(BaseModel):
    ui_language: Optional[str] = None
    whisper_backend: Optional[str] = None
    whisper_model: Optional[str] = None
    vibe_model: Optional[str] = None  # vibe-server の文字起こしモデル（設定の vibe_model）
    whisper_language: Optional[str] = None


@app.get("/wizard/status")
async def wizard_status():
    """ウィザード完了状態を返す"""
    config = load_config()
    return {
        "wizard_completed": config.wizard_completed,
    }


@app.post("/wizard/save")
async def wizard_save(request: WizardSaveRequest):
    """ウィザード設定を保存し、wizard_completed=trueに設定"""
    config = load_config()

    if request.ui_language is not None:
        if request.ui_language not in i18n.SUPPORTED_LANGUAGES:
            raise HTTPException(status_code=400, detail=t("cfg.invalid_language", value=request.ui_language))
        prompt_sets.switch_language(config, request.ui_language)
    if request.whisper_backend is not None:
        config.whisper_backend = request.whisper_backend
    if request.whisper_model is not None:
        config.whisper_model = request.whisper_model
    vibe_model_changed = False
    if request.vibe_model is not None:
        if not is_valid_vibe_model(request.vibe_model):
            raise HTTPException(status_code=400, detail=t("cfg.invalid_vibe_model", value=request.vibe_model))
        vibe_model_changed = normalize_model_name(request.vibe_model) != normalize_model_name(config.vibe_model)
        config.vibe_model = normalize_model_name(request.vibe_model)
    if request.whisper_language is not None:
        config.whisper_language = request.whisper_language

    config.wizard_completed = True

    try:
        save_config(config)
        logger.info("ウィザード設定を保存しました")
        # Vibe が見つかっていれば、メイン画面へ進む前に再起動なしで使える状態にしておく
        # （モデルのダウンロード中などで使えなくても、保存自体は成功扱い）
        found = await asyncio.get_event_loop().run_in_executor(
            executor, vibe_client.find_vibe_binary, config.vibe_server_path or "")
        if vibe_model_changed:
            # 選んだモデルで vibe-server を起動し直す（未取得なら、取得済みのモデルで動かして案内を出す）
            _apply_vibe_model_change()
        elif found is not None:
            task = asyncio.create_task(_activate_vibe_quietly())
            _background_tasks.add(task)  # 完了まで参照を保持する（回収されないように）
            task.add_done_callback(_background_tasks.discard)
        return {"status": "ok", "message": t("wizard.saved")}
    except Exception as e:
        logger.error(f"ウィザード設定保存エラー: {e}")
        return {"status": "error", "message": t("cfg.save_failed", error=e)}


@app.api_route("/{full_path:path}", methods=["GET"])
async def serve_frontend(full_path: str):
    """静的なファイルを配信し、見つからない場合は index.html を返す"""
    file_path = (STATIC_DIR / full_path).resolve()
    if not str(file_path).startswith(str(STATIC_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Forbidden")
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"error": "Not found"}


# エントリポイントは root main.py から行う
