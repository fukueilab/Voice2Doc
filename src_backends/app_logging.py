"""アプリ活動ログ

起動ごとに ``logs/YYYY-MM-DDTHH-MM-SS.log``（例: 2026-10-28T21-22-31.log）を作成し、
不具合・トラブル時の解析に使えるよう、アプリ・uvicorn・未処理例外をすべて同じファイルへ記録する。

- ウィンドウアプリ（PyInstaller ``console=False``）では ``sys.stdout`` / ``sys.stderr`` が ``None`` になるため、
  ログファイルへ接続して ``None`` 参照（uvicornの ``sys.stdout.isatty()`` 等）による起動失敗を防ぐ。
- プロンプト本文・文字起こし本文などのユーザーコンテンツはログに出さない（文字数のみ記録する）。
"""
import faulthandler
import logging
import platform
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config as _config

LOG_DIR = _config.BASE_DIR / "logs"
LOG_FILE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(_\d+)?\.log$")
MAX_LOG_FILES = 30
LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(threadName)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("voice2doc.app_logging")

# setup_logging が変更した状態（shutdown_logging で元へ戻す）
_state: dict = {}


class _PollingAccessFilter(logging.Filter):
    """進捗表示のため1秒前後で繰り返される GET /status・/health の成功ログを除外する（ログ肥大化の防止）"""

    _NOISY = ('"GET /status ', '"GET /health ')

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        message = record.getMessage()
        return not (any(p in message for p in self._NOISY) and " 200" in message)


def build_log_path(log_dir: Path, now: Optional[datetime] = None) -> Path:
    """起動時刻から ``YYYY-MM-DDTHH-MM-SS.log`` のパスを作る（同秒で既存なら ``_1`` 以降を付与）"""
    stamp = (now or datetime.now()).strftime("%Y-%m-%dT%H-%M-%S")
    path = log_dir / f"{stamp}.log"
    suffix = 1
    while path.exists():
        path = log_dir / f"{stamp}_{suffix}.log"
        suffix += 1
    return path


def prune_old_logs(log_dir: Path, keep: int = MAX_LOG_FILES) -> None:
    """ログファイルが ``keep`` 件を超えたら古いものから削除する（命名規則に一致するファイルのみ対象）"""
    try:
        files = sorted(
            (p for p in Path(log_dir).iterdir() if p.is_file() and LOG_FILE_PATTERN.match(p.name)),
            key=lambda p: p.name,
        )
    except OSError:
        return
    for old in files[: max(len(files) - max(keep, 0), 0)]:
        try:
            old.unlink()
        except OSError:
            pass


def log_unhandled_exception(exc_type, exc_value, exc_tb) -> None:
    """未処理例外をトレースバック付きでログに記録する（sys.excepthook / threading.excepthook 用）"""
    if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
        return
    logging.getLogger("voice2doc").critical(
        "未処理の例外が発生しました", exc_info=(exc_type, exc_value, exc_tb)
    )


def _thread_excepthook(args) -> None:
    thread_name = args.thread.name if args.thread else "?"
    logging.getLogger("voice2doc").critical(
        f"スレッド '{thread_name}' で未処理の例外が発生しました",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def setup_logging(
    log_dir: Optional[Path] = None,
    install_hooks: bool = True,
    keep: int = MAX_LOG_FILES,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """ログ出力を初期化して今回のログファイルのパスを返す（冪等）。

    ログフォルダを作れない等の場合でもアプリ起動は妨げず ``None`` を返す。
    """
    if _state.get("path") is not None:
        return _state["path"]

    target_dir = Path(log_dir) if log_dir is not None else LOG_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        prune_old_logs(target_dir, keep=max(keep - 1, 0))
        path = build_log_path(target_dir, now)
        file_handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        return None

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_PollingAccessFilter())

    root = logging.getLogger()
    handlers = [file_handler]
    # 標準エラーが実在するとき（開発時のターミナル実行）だけコンソールにも出す
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    _state["root_level"] = root.level
    _state["handlers"] = handlers
    _state["replaced_streams"] = {}
    root.setLevel(logging.INFO)
    for h in handlers:
        root.addHandler(h)

    # ウィンドウアプリでは標準出力/標準エラーが None。ログファイルへ接続して None 参照を防ぐ
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            _state["replaced_streams"][name] = None
            setattr(sys, name, file_handler.stream)

    logging.captureWarnings(True)

    # pywebview は import 時に自前の StreamHandler を付ける。console=False の exe ではその時点の
    # sys.stderr が None のため、以後のログごとに "--- Logging error ---" が出る。外して root へ流す
    pywebview_logger = logging.getLogger("pywebview")
    for h in list(pywebview_logger.handlers):
        if type(h) is logging.StreamHandler:
            pywebview_logger.removeHandler(h)

    if install_hooks:
        _state["orig_excepthook"] = sys.excepthook
        _state["orig_thread_excepthook"] = threading.excepthook
        sys.excepthook = log_unhandled_exception
        threading.excepthook = _thread_excepthook
        try:
            faulthandler.enable(file=file_handler.stream, all_threads=True)
            _state["faulthandler"] = True
        except (RuntimeError, ValueError, OSError):
            pass

    _state["path"] = path
    logger.info(f"ログファイル: {path}")
    return path


def shutdown_logging() -> None:
    """setup_logging の変更を元に戻す（主にテスト用）"""
    root = logging.getLogger()
    for h in _state.get("handlers", []):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    if "root_level" in _state:
        root.setLevel(_state["root_level"])
    for name, original in _state.get("replaced_streams", {}).items():
        setattr(sys, name, original)
    if "orig_excepthook" in _state:
        sys.excepthook = _state["orig_excepthook"]
    if "orig_thread_excepthook" in _state:
        threading.excepthook = _state["orig_thread_excepthook"]
    if _state.get("faulthandler"):
        try:
            faulthandler.disable()
        except Exception:
            pass
    logging.captureWarnings(False)
    _state.clear()


# faulthandler が書く COM 呼び出し競合の例外コード。WebView2 の利用中に出ても動作は続くことが多い
_COM_CALL_CONFLICT = "Windows fatal exception: code 0x8001010d"


def note_known_fatal_codes(log_path: Optional[Path] = None) -> bool:
    """ログに faulthandler の ``0x8001010d`` が書かれていたら、通常のログに注記を1行だけ残す（原因は断定しない）。

    faulthandler の出力は下位で直接書かれ、ログの整形・検索の対象になりにくいため、後から拾って通常の行にする。
    記録したら True（同じ起動では2回目以降は何もしない）。
    """
    if _state.get("com_noted"):
        return False
    path = log_path if log_path is not None else _state.get("path")
    if not path:
        return False
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if _COM_CALL_CONFLICT not in text:
        return False
    _state["com_noted"] = True
    logging.getLogger("voice2doc").warning(
        "faulthandlerが COM 呼び出しの競合(0x8001010d)を記録しました。"
        "WebView2の利用中に出てもアプリは動き続けることが多く（無害な場合が多い）、画面が白くなる原因とは断定できません"
    )
    return True


def log_startup_info() -> None:
    """起動時の環境情報をログに記録する（トラブル解析用）"""
    log = logging.getLogger("voice2doc")
    log.info("=" * 60)
    log.info(f"話書き (Voice2Doc) v{_config.APP_VERSION} 起動")
    log.info(f"Python {platform.python_version()} / {platform.platform()}")
    log.info(f"frozen={getattr(sys, 'frozen', False)} / 実行ファイル={sys.executable}")
    log.info(f"データフォルダ: {_config.BASE_DIR}")
    log.info(f"ログフォルダ: {LOG_DIR}")
    log.info("=" * 60)
