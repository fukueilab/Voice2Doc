"""起動処理の純粋なロジック（起動時に画面が真っ白のままになる問題の対策）

``main.py``（PyWebView の起動・ウィンドウ操作）から切り離し、GUI なしで単体テストできるようにしてある。

- 自分のサーバーかの識別（``APP_ID`` / ``INSTANCE_ID``）と、起動待機 ``wait_for_server``
- ポートの調査と選択 ``select_port``
- WebView2 のデータフォルダ ``run-<pid>`` の整理 ``cleanup_stale_userdata``
- 画面からの「表示できた」合図 ``/ui-ready`` の状態と、見張り役の判定 ``run_watchdog``
- 描画の安全モード（GPU 無効）のマーカーと環境変数
- 起動中・失敗時に見せるスプラッシュ画面の HTML
"""
import ctypes
import html
import json
import logging
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import config as _config

logger = logging.getLogger("voice2doc.startup")

HOST = "127.0.0.1"
DEFAULT_PORT = 8008
LAST_PORT = 8030
APP_ID = "voice2doc"
# プロセスの起動ごとに作る識別子。/health で返し、起動待機・/ui-ready で「自分のサーバーか」を確かめる
INSTANCE_ID = uuid.uuid4().hex

# 画面からの合図を待つ時間（秒）
UI_READY_FIRST_WAIT = 10.0
UI_READY_RELOAD_WAIT = 8.0

SAFE_RENDER_ENV = "VOICE2DOC_SAFE_RENDER"
WEBVIEW2_ARGS_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
SAFE_RENDER_ARG = "--disable-gpu"
# テスト専用の環境変数（実利用では設定しない）
TEST_IGNORE_UI_READY_ENV = "VOICE2DOC_TEST_IGNORE_UI_READY"
TEST_STARTUP_DELAY_ENV = "VOICE2DOC_TEST_STARTUP_DELAY"

# 待機結果
WAIT_OK = "ok"
WAIT_THREAD_DIED = "thread_died"
WAIT_TIMEOUT = "timeout"

# ポートの使用者
PORT_FREE = "free"
PORT_VOICE2DOC = "voice2doc"
PORT_OTHER = "other"

# 見張り役の結果
WATCHDOG_READY = "ready"
WATCHDOG_READY_AFTER_RELOAD = "ready_after_reload"
WATCHDOG_RELAUNCHED = "relaunched"
WATCHDOG_FAILED = "failed"


# ==================== 画面からの合図（/ui-ready） ====================

_ui_ready = threading.Event()


def mark_ui_ready() -> None:
    _ui_ready.set()


def reset_ui_ready() -> None:
    _ui_ready.clear()


def is_ui_ready() -> bool:
    return _ui_ready.is_set()


def wait_ui_ready(timeout: float) -> bool:
    return _ui_ready.wait(timeout)


def ignore_ui_ready_for_test() -> bool:
    """テスト専用: サーバーが /ui-ready を無視する（画面が合図を送れない状況の再現）"""
    return os.environ.get(TEST_IGNORE_UI_READY_ENV) == "1"


def test_startup_delay() -> float:
    """テスト専用: 起動を遅くする秒数（スプラッシュの確認用）"""
    try:
        return max(float(os.environ.get(TEST_STARTUP_DELAY_ENV, "0")), 0.0)
    except ValueError:
        return 0.0


# ==================== ポート ====================

def _fetch_health(port: int, timeout: float = 2.0) -> Optional[dict]:
    """/health を取得して辞書で返す。失敗・JSONでない場合は None"""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 接続拒否・タイムアウト・JSON以外はすべて「取れなかった」
        return None


def probe_port(port: int, host: str = HOST, fetch_health: Callable[[int], Optional[dict]] = _fetch_health) -> str:
    """ポートの使用者を調べる: ``PORT_FREE`` / ``PORT_VOICE2DOC`` / ``PORT_OTHER``"""
    held = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        held = sock.connect_ex((host, port)) == 0
    if held:
        data = fetch_health(port)
        if data and data.get("app") == APP_ID:
            return PORT_VOICE2DOC
        return PORT_OTHER
    # 接続できなくても、他のソフトが排他的に確保している場合がある。bind できるかで確かめる
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return PORT_OTHER
    return PORT_FREE


@dataclass
class PortSelection:
    port: Optional[int]  # 使うポート。空きが無ければ None
    first_holder: str  # 最初の候補(8008)の使用者（PORT_FREE / PORT_VOICE2DOC / PORT_OTHER）


def select_port(
    first: int = DEFAULT_PORT,
    last: int = LAST_PORT,
    probe: Callable[[int], str] = probe_port,
) -> PortSelection:
    """``first`` から ``last`` まで順に調べ、最初の空きポートを返す"""
    first_holder = PORT_FREE
    for port in range(first, last + 1):
        state = probe(port)
        if port == first:
            first_holder = state
        if state == PORT_FREE:
            return PortSelection(port, first_holder)
    return PortSelection(None, first_holder)


def build_origins(port: int) -> list:
    """CORS の許可元（そのポートの 127.0.0.1 / localhost のみ）"""
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


# ==================== 起動待機 ====================

def wait_for_server(
    port: int,
    instance_id: str,
    is_alive: Callable[[], bool],
    timeout: float = 120.0,
    interval: float = 0.5,
    fetch_health: Callable[[int], Optional[dict]] = _fetch_health,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """自分のサーバーが応答するまで待つ。

    uvicorn のスレッドが生きていて、/health の ``instance_id`` が自分のものと一致したときだけ ``WAIT_OK``。
    他のプロセスが同じポートで応答していても成功にしない。スレッドが落ちたら待たずに ``WAIT_THREAD_DIED``。
    """
    deadline = clock() + timeout
    while True:
        if not is_alive():
            return WAIT_THREAD_DIED
        data = fetch_health(port)
        if data and data.get("app") == APP_ID and data.get("instance_id") == instance_id:
            return WAIT_OK
        if clock() >= deadline:
            return WAIT_TIMEOUT
        sleep(interval)


# ==================== WebView2 のデータフォルダ ====================

_RUN_DIR = re.compile(r"^run-(\d+)$")


def userdata_root() -> Path:
    return Path(os.path.expanduser("~")) / ".voice2doc"


def userdata_dir(pid: Optional[int] = None) -> Path:
    """この起動専用の WebView2 データフォルダ。前回の WebView2 が残っていても影響を受けない"""
    return userdata_root() / f"run-{pid if pid is not None else os.getpid()}"


def pid_alive(pid: int) -> bool:
    """プロセスが動いているか。判定できなければ安全側（生きている）に倒す"""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            # アクセス拒否(5)は存在するが開けないだけ。それ以外(87: 無効なパラメーター)は存在しない
            return ctypes.get_last_error() == 5
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT = まだ動いている
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return True


def cleanup_stale_userdata(
    root: Optional[Path] = None,
    own_pid: Optional[int] = None,
    is_alive: Callable[[int], bool] = pid_alive,
) -> list:
    """動いていないプロセスの ``run-<pid>`` を削除する。削除できたフォルダ名のリストを返す。

    生きているプロセスのもの・自分のもの・命名規則に合わないものは触らない。削除に失敗しても無視する。
    """
    base = Path(root) if root is not None else userdata_root()
    own = own_pid if own_pid is not None else os.getpid()
    removed = []
    try:
        entries = list(base.iterdir())
    except OSError:
        return removed
    for entry in entries:
        match = _RUN_DIR.match(entry.name)
        if not match or not entry.is_dir():
            continue
        pid = int(match.group(1))
        if pid == own or is_alive(pid):
            continue
        try:
            shutil.rmtree(entry)
            removed.append(entry.name)
        except OSError as e:
            logger.warning(f"古い WebView2 データフォルダを削除できませんでした（無視します）: {entry.name}: {e}")
    return removed


# ==================== 描画の安全モード ====================

def safe_render_marker() -> Path:
    return _config.BASE_DIR / "safe_render"


def safe_render_marker_exists() -> bool:
    return safe_render_marker().exists()


def write_safe_render_marker() -> bool:
    try:
        marker = safe_render_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("GPU acceleration disabled for the WebView2 window\n", encoding="utf-8")
        return True
    except OSError as e:
        logger.warning(f"安全モードのマーカーを作れませんでした: {e}")
        return False


def clear_safe_render_marker() -> bool:
    try:
        safe_render_marker().unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError as e:
        logger.warning(f"安全モードのマーカーを削除できませんでした: {e}")
        return False


def safe_render_requested(environ=None) -> bool:
    """安全モードで起動するか（再起動で付いた環境変数、または成功時に残したマーカー）"""
    env = os.environ if environ is None else environ
    return env.get(SAFE_RENDER_ENV) == "1" or safe_render_marker_exists()


def with_safe_render_env(environ=None) -> dict:
    """GPU を無効にする WebView2 の引数を加えた環境変数の複製を返す"""
    env = dict(os.environ if environ is None else environ)
    current = env.get(WEBVIEW2_ARGS_ENV, "")
    if SAFE_RENDER_ARG not in current.split():
        env[WEBVIEW2_ARGS_ENV] = (current + " " + SAFE_RENDER_ARG).strip()
    env[SAFE_RENDER_ENV] = "1"
    return env


# ==================== 見張り役 ====================

def run_watchdog(
    wait_ready: Callable[[float], bool],
    diagnose: Callable[[], None],
    reload_page: Callable[[], None],
    relaunch_safe: Callable[[], bool],
    show_failure: Callable[[], None],
    already_safe: bool,
    first_wait: float = UI_READY_FIRST_WAIT,
    reload_wait: float = UI_READY_RELOAD_WAIT,
) -> str:
    """画面からの合図を待ち、来なければ 診断 → 再読み込み → 安全モードで再起動 → 案内 の順に進める。

    すべての手順を引数で受け取るため、実際に待たずに判定だけを検査できる。
    ``relaunch_safe`` は再起動を始められたら True（安全モードの再起動は ``already_safe`` で1回だけに制限）。
    """
    if wait_ready(first_wait):
        return WATCHDOG_READY
    logger.warning(f"画面からの合図が{first_wait:g}秒以内に来ませんでした。診断を記録して再読み込みします")
    try:
        diagnose()
    except Exception:  # noqa: BLE001 - 診断の失敗で復旧を止めない
        logger.warning("診断の取得に失敗しました", exc_info=True)
    try:
        reload_page()
    except Exception:  # noqa: BLE001
        logger.warning("再読み込みに失敗しました", exc_info=True)
    if wait_ready(reload_wait):
        logger.info("再読み込み後に画面からの合図を受け取りました")
        return WATCHDOG_READY_AFTER_RELOAD
    if not already_safe:
        logger.warning("再読み込みでも直らないため、GPU を使わない描画の安全モードで作り直します")
        try:
            if relaunch_safe():
                return WATCHDOG_RELAUNCHED
        except Exception:  # noqa: BLE001
            logger.error("安全モードでの再起動に失敗しました", exc_info=True)
    else:
        logger.error("安全モードでも画面を表示できませんでした")
    show_failure()
    return WATCHDOG_FAILED


def evaluate_with_timeout(fn: Callable, timeout: float = 5.0):
    """``fn()`` を別スレッドで実行し、``timeout`` 秒以内に返った値を返す。固まったら None（描画できない場合の保険）"""
    box: dict = {}

    def runner():
        try:
            box["value"] = fn()
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    thread = threading.Thread(target=runner, daemon=True, name="evaluate-js")
    thread.start()
    thread.join(timeout)
    return box.get("value")


def describe_environment() -> dict:
    """白い画面の診断に使う環境情報（WebView2・pywebview・GPU）。取れない項目は「不明」"""
    info = {"WebView2 Runtime": "不明", "pywebview": "不明", "GPU": "不明"}
    try:
        import winreg

        guid = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for hive, sub in (
            (winreg.HKEY_LOCAL_MACHINE, guid),
            (winreg.HKEY_CURRENT_USER, guid.replace("WOW6432Node\\", "")),
        ):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    info["WebView2 Runtime"] = str(winreg.QueryValueEx(key, "pv")[0])
                    break
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    try:
        from importlib.metadata import version

        info["pywebview"] = version("pywebview")
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController | "
             "ForEach-Object { $_.Name + ' / ' + $_.DriverVersion }) -join '; '"],
            capture_output=True, text=True, timeout=8, creationflags=0x08000000,
        ).stdout.strip()
        if out:
            info["GPU"] = out
    except Exception:  # noqa: BLE001
        pass
    return info


# ==================== スプラッシュ ====================

def detect_os_language() -> str:
    """OS の表示言語（日本語なら ja、それ以外は en）"""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        return "ja" if (lang_id & 0xFF) == 0x11 else "en"
    except Exception:  # noqa: BLE001
        import locale

        return "ja" if (locale.getlocale()[0] or "").lower().startswith("ja") else "en"


_SPLASH_STYLE = """
html,body{height:100%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:#f5f6f8;color:#222;
font-family:"Segoe UI","Yu Gothic UI",Meiryo,sans-serif}
@media (prefers-color-scheme:dark){body{background:#1e1f22;color:#e6e6e6}}
.box{max-width:560px;padding:24px;text-align:center}
.spin{width:44px;height:44px;margin:0 auto 18px;border:4px solid #c8ccd4;border-top-color:#6c4cf1;
border-radius:50%;animation:s 1s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}
h1{font-size:20px;margin:0 0 10px}
p{margin:8px 0;line-height:1.6;font-size:14px;word-break:break-all}
.err{color:#b3261e}
.path{font-size:12px;opacity:.75}
button{margin-top:14px;padding:8px 22px;font-size:14px;border:0;border-radius:6px;background:#6c4cf1;color:#fff;
cursor:pointer}
"""


def build_splash_html(lang: str, error: Optional[str] = None, log_dir: Optional[str] = None) -> str:
    """起動中（``error`` なし）または起動失敗（原因・ログの場所・再試行ボタン）の画面。ネットワーク不要の単一 HTML"""
    from .i18n import tr

    lang = lang if lang in ("ja", "en") else "ja"
    if error is None:
        body = (
            '<div class="spin"></div>'
            f"<h1>{html.escape(tr(lang, 'boot.starting'))}</h1>"
            f"<p>{html.escape(tr(lang, 'boot.starting_hint'))}</p>"
        )
    else:
        log_line = ""
        if log_dir:
            log_line = f'<p class="path">{html.escape(tr(lang, "boot.log_location", path=log_dir))}</p>'
        body = (
            f'<h1 class="err">{html.escape(tr(lang, "boot.failed_title"))}</h1>'
            f"<p>{html.escape(error)}</p>{log_line}"
            f'<button onclick="if(window.pywebview&&pywebview.api)pywebview.api.retry()">'
            f'{html.escape(tr(lang, "boot.retry"))}</button>'
        )
    return (
        f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="UTF-8">'
        f"<title>Voice2Doc</title><style>{_SPLASH_STYLE}</style></head>"
        f'<body><div class="box">{body}</div></body></html>'
    )
