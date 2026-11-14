"""話書き (Voice2Doc) - PyWebView版 エントリポイント

起動の流れ:
1. 二重起動の防止 → ウィンドウを先に作り、「起動しています…」のスプラッシュを表示する
2. GUI が動いたあと（``webview.start(func=boot)``）に、前提条件のチェック・設定の読み込み・
   サーバーの起動と待機を行い、準備ができたら ``load_url`` で本画面へ切り替える
3. 本画面が「表示できた」合図（``POST /ui-ready``）を出さないときは、見張り役が
   診断 → 再読み込み → GPU 無効の安全モードで作り直し → 案内 の順で立て直す
判定ロジックは ``src_backends/startup.py`` にあり、GUI なしで単体テストできる。
"""
import sys
import os
import threading
import logging
import subprocess
import time

# カレントディレクトリと src_backends をモジュール探索パスに追加
_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "src_backends"))

# 二重起動の防止（最優先）。2つ目のプロセスは重いimportやログファイルの作成を行わず、
# 起動済みのウィンドウを前面に出して無言で終了する（console=False のため表示手段は無い）
if __name__ == "__main__":
    from src_backends.single_instance import acquire, bring_existing_to_front

    if not acquire():
        bring_existing_to_front()
        sys.exit(0)

# isort: off
from src_backends import startup  # noqa: E402

# 安全モード（GPU を使わない描画）: 再起動で付いた環境変数、または成功時に残したマーカーがあれば、
# WebView2 の初期化より前に引数を環境変数へ入れる。_SAFE_RELAUNCHED は「自動の再起動で来た」ことを表す
_SAFE_RELAUNCHED = os.environ.get(startup.SAFE_RENDER_ENV) == "1"
_SAFE_ACTIVE = startup.safe_render_requested()
if _SAFE_ACTIVE:
    os.environ.update(startup.with_safe_render_env())

import webview  # noqa: E402

# ログ出力（logs/YYYY-MM-DDTHH-MM-SS.log）は他のアプリモジュールより先に初期化する。
# src_backends.main などが import 時にロガーを使うため、ここで root にハンドラを付けておく必要がある
from src_backends import app_logging  # noqa: E402
from src_backends.app_logging import setup_logging, log_startup_info  # noqa: E402
from src_backends.path_utils import get_app_dir  # noqa: E402
from src_backends.i18n import tr  # noqa: E402

setup_logging()
log_startup_info()

# isort: on

logger = logging.getLogger("voice2doc")

HOST = startup.HOST
MAIN_TITLE = "話書き - Voice2Doc"

# 実行中の状態（ウィンドウ・サーバー・選んだポート・表示言語）
_ctx: dict = {
    "window": None,
    "server": None,
    "server_thread": None,
    "server_error": None,
    "port": startup.DEFAULT_PORT,
    "lang": startup.detect_os_language(),
}
_closing = threading.Event()
_boot_done = threading.Event()


class _BootAborted(Exception):
    """起動の途中でウィンドウが閉じられた"""


def _abort_if_closing() -> None:
    if _closing.is_set():
        raise _BootAborted()


class BootError(Exception):
    """起動を続けられない原因（利用者に見せる文言を持つ）"""


# ==================== メッセージ表示 ====================

def show_message_box(title: str, text: str) -> None:
    """黙って終了しないためのメッセージボックス（console=False のため他に表示手段が無い）"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        logger.warning("メッセージボックスを表示できませんでした", exc_info=True)


def _log_dir() -> str:
    return str(app_logging.LOG_DIR)


# ==================== サーバー ====================

def start_server(port: int):
    """FastAPIサーバーを別スレッドで起動する。(server, thread) を返す"""
    import uvicorn

    from src_backends.main import app

    logger.info(f"FastAPIサーバー起動: {HOST}:{port}")
    # log_config=None: uvicornの独自ログ設定(標準出力へ出力)を使わず、rootのファイルハンドラへ流す
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="info", log_config=None))

    def run():
        try:
            server.run()
        except BaseException as e:  # noqa: BLE001 - バインド失敗は SystemExit で来る。原因を残してスレッドを終える
            _ctx["server_error"] = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.error(f"サーバーが終了しました: {_ctx['server_error']}", exc_info=not isinstance(e, SystemExit))

    thread = threading.Thread(target=run, daemon=True, name="uvicorn")
    thread.start()
    return server, thread


def stop_server(timeout: float = 5.0) -> None:
    """uvicorn を止めてポートを解放する（should_exit を立てて、スレッドの終了を少し待つ）"""
    server = _ctx.get("server")
    thread = _ctx.get("server_thread")
    if server is not None:
        server.should_exit = True
    if thread is not None and thread.is_alive():
        thread.join(timeout)


def shutdown_children():
    """子プロセス(vibe-server.exe)を確実に停止する。

    GUIループ終了後はuvicornのデーモンスレッドごとプロセスが終わり、lifespanの終了処理
    (stop_vibe)が走らない場合があるため、ここで明示的に停止する。
    """
    try:
        from src_backends import vibe_client

        vibe_client.stop_vibe()
    except Exception:
        logger.warning("vibe-serverの停止に失敗しました", exc_info=True)


def on_closed():
    """ウィンドウが閉じられたときの処理。

    closing は「終了しますか」の確認でキャンセルされても呼ばれるため、終了の判定には使わない
    """
    _closing.set()
    logger.info("アプリケーション終了")


# ==================== 自己再起動 ====================

def relaunch(env: dict) -> bool:
    """自分自身を ``env`` で起動し直し、このプロセスのウィンドウを閉じる。起動できたら True。

    新しいプロセスが二重起動と判定されないよう、サーバーと vibe-server を止め、ミューテックスを解放してから起動する。
    """
    from src_backends import single_instance

    if getattr(sys, "frozen", False):
        env = dict(env, PYINSTALLER_RESET_ENVIRONMENT="1")  # 起動し直すプロセスが古い展開先の設定を引き継がない
    stop_server()
    shutdown_children()
    single_instance.release()
    cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
    try:
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(cmd, env=env, close_fds=True, creationflags=flags if sys.platform == "win32" else 0)
    except OSError:
        logger.error("自己再起動に失敗しました。このプロセスを続けるため、サーバーと二重起動の防止を元に戻します",
                     exc_info=True)
        single_instance.acquire()
        try:
            _ctx["server"], _ctx["server_thread"] = start_server(_ctx["port"])
        except Exception:
            logger.error("サーバーの再起動にも失敗しました", exc_info=True)
        return False
    logger.info("自己再起動しました。このプロセスを終了します")
    window = _ctx.get("window")
    if window is not None:
        try:
            window.confirm_close = False  # 「終了しますか」の確認を出さずに閉じる（確認でキャンセルされると二重に残る）
            window.destroy()
        except Exception:
            logger.warning("ウィンドウを閉じられませんでした", exc_info=True)
    return True


class Api:
    """スプラッシュ画面のボタンから呼ぶ操作（js_api）"""

    def retry(self):
        """起動に失敗したときの「もう一度試す」: アプリを起動し直す"""
        logger.info("利用者が再試行を選びました")
        env = dict(os.environ)
        if not relaunch_with(env):
            window = _ctx.get("window")
            if window is not None:
                window.load_html(startup.build_splash_html(
                    _ctx["lang"], tr(_ctx["lang"], "boot.err_generic", error="relaunch"), _log_dir()))


def relaunch_with(env: dict) -> bool:
    # js_api の呼び出しスレッドを塞がないよう、実処理は別スレッドで行う
    result: dict = {}

    def run():
        result["ok"] = relaunch(env)

    thread = threading.Thread(target=run, daemon=True, name="relaunch")
    thread.start()
    thread.join(30)
    return bool(result.get("ok"))


# ==================== 見張り役（白い画面の検知と復旧） ====================

def _diagnose(window) -> None:
    """表示できていない画面の状態をログに残す（再発時に原因を絞り込むため）"""
    script = (
        "JSON.stringify({readyState: document.readyState, href: location.href,"
        " bodyLength: document.body ? document.body.innerHTML.length : -1, userAgent: navigator.userAgent})"
    )
    value = startup.evaluate_with_timeout(lambda: window.evaluate_js(script), 5.0)
    if value:
        logger.warning(f"診断: 画面の状態 {value}")
    else:
        logger.warning("診断: 画面から応答がありません（描画できていない可能性があります）")
    def describe():
        for name, text in startup.describe_environment().items():
            logger.warning(f"診断: {name}: {text}")

    # GPU の問い合わせは数秒かかるため、再読み込みを遅らせないよう別スレッドで記録する
    threading.Thread(target=describe, daemon=True, name="diagnose-env").start()
    app_logging.note_known_fatal_codes()


def _wait_ready(timeout: float) -> bool:
    """合図を待つ。ウィンドウが閉じられたら待たずに抜ける（閉じた後に復旧処理をしない）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _closing.is_set():
            return True
        if startup.wait_ui_ready(min(0.5, max(deadline - time.monotonic(), 0))):
            return True
    return _closing.is_set()


def _show_blank_failure() -> None:
    lang = _ctx["lang"]
    show_message_box(tr(lang, "boot.blank_title"), tr(lang, "boot.blank_body", path=_log_dir()))


def watch_ui(window, url: str) -> None:
    """本画面を開いてから、画面の合図を見張る"""

    def reload_page():
        startup.reset_ui_ready()
        window.load_url(url)

    outcome = startup.run_watchdog(
        wait_ready=_wait_ready,
        diagnose=lambda: _diagnose(window),
        reload_page=reload_page,
        relaunch_safe=lambda: relaunch(startup.with_safe_render_env()),
        show_failure=_show_blank_failure,
        already_safe=_SAFE_ACTIVE,
    )
    logger.info(f"画面の見張り結果: {outcome}")
    if outcome in (startup.WATCHDOG_READY, startup.WATCHDOG_READY_AFTER_RELOAD):
        # 自動の安全モード再起動で表示できたときだけ、以後も最初から安全モードにする
        if _SAFE_RELAUNCHED and not startup.safe_render_marker_exists() and not _closing.is_set():
            if startup.write_safe_render_marker():
                logger.warning("安全モード(GPU無効)で表示できたため、以後も安全モードで起動します（設定から解除できます）")
        app_logging.note_known_fatal_codes()


# ==================== 起動処理（スプラッシュの裏で実行） ====================

def _show_boot_error(message: str) -> None:
    """起動に失敗した原因・ログの場所・再試行ボタンをスプラッシュ画面に出す"""
    window = _ctx.get("window")
    if window is None:
        show_message_box(tr(_ctx["lang"], "boot.msgbox_title"), f"{message}\n\n{_log_dir()}")
        return
    window.load_html(startup.build_splash_html(_ctx["lang"], message, _log_dir()))


def boot() -> None:
    """GUI が動き出したあとにバックグラウンドで実行する"""
    try:
        _boot_inner()
    except _BootAborted:
        logger.info("起動中にウィンドウが閉じられたため、起動を中止しました")
    except BootError as e:
        logger.error(f"起動に失敗しました: {e}")
        _show_boot_error(str(e))
    except Exception as e:
        logger.critical(f"起動に失敗しました: {e}", exc_info=True)
        _show_boot_error(tr(_ctx["lang"], "boot.err_generic", error=e))
    finally:
        _boot_done.set()


def _boot_inner() -> None:
    window = _ctx["window"]

    # テスト専用: 起動が長引く状況（スプラッシュが出続けること）の再現
    delay = startup.test_startup_delay()
    if delay:
        logger.info(f"テスト用に起動を{delay:g}秒遅らせます")
        time.sleep(delay)

    _abort_if_closing()
    # 重いモジュールはここで読み込む（ウィンドウとスプラッシュを先に出すため）
    from src_backends.config import load_config
    from src_backends.main import configure_cors
    from src_backends.setup_checker import ensure_all_prerequisites

    # 前提条件チェック (VC++ Runtime, WebView2, FFmpeg, vibe-server.exe)
    logger.info("前提条件チェックを開始します...")
    prereq_ok = ensure_all_prerequisites(
        progress_callback=lambda msg: logger.info(f"前提条件: {msg}")
    )
    if not prereq_ok:
        logger.warning("一部の前提条件が満たされていません。アプリケーションを続行しますが、正常に動作しない場合があります。")

    # 設定読み込み
    config = load_config()
    _ctx["lang"] = config.ui_language if config.ui_language in ("ja", "en") else _ctx["lang"]
    logger.info(f"Whisperモデル: {config.whisper_model}")
    logger.info(f"ウィザード完了: {config.wizard_completed}")
    logger.info(f"AIモデル: {config.llm_model_path or '既定(gemma4:e2b-qat)'}")
    from src_backends import vibe_client

    logger.info(f"vibe-server.exe: {vibe_client.find_vibe_binary(config.vibe_server_path) or '見つかりません'}")

    _abort_if_closing()
    # ポートの選択。既定のポートを他のソフトや終了中の前回のプロセスが使っていたら、空きポートへ移る
    selection = startup.select_port()
    if selection.first_holder != startup.PORT_FREE:
        logger.warning(
            f"ポート{startup.DEFAULT_PORT}は使用中です（使用者: {selection.first_holder}）。"
            f"空きポート{selection.port}を使います"
        )
    if selection.port is None:
        raise BootError(tr(_ctx["lang"], "boot.err_port_full"))
    port = selection.port
    _ctx["port"] = port
    configure_cors(port)

    # FastAPIサーバーを別スレッドで起動し、自分のサーバーが応答するまで待つ
    _abort_if_closing()
    server, thread = start_server(port)
    _ctx["server"], _ctx["server_thread"] = server, thread
    result = startup.wait_for_server(port, startup.INSTANCE_ID, thread.is_alive)
    if _closing.is_set():
        stop_server()
        shutdown_children()
        raise _BootAborted()
    if result == startup.WAIT_THREAD_DIED:
        raise BootError(tr(_ctx["lang"], "boot.err_server_died", error=_ctx["server_error"] or "?"))
    if result == startup.WAIT_TIMEOUT:
        raise BootError(tr(_ctx["lang"], "boot.err_timeout"))
    logger.info("サーバー起動確認完了")

    # 本画面へ切り替える（ウィザード未完了なら初回設定ウィザード）
    if not config.wizard_completed:
        logger.info("初回起動ウィザードを表示します")
        url = f"http://{HOST}:{port}/wizard.html"
        window.set_title(tr(_ctx["lang"], "boot.wizard_title"))
    else:
        url = f"http://{HOST}:{port}"
    startup.reset_ui_ready()
    window.load_url(url)
    threading.Thread(target=watch_ui, args=(window, url), daemon=True, name="ui-watchdog").start()


# ==================== エントリポイント ====================

def main():
    try:
        _main_inner()
    except Exception as e:
        logger.critical(f"起動に失敗しました: {e}", exc_info=True)
        show_message_box(
            tr(_ctx["lang"], "boot.msgbox_title"),
            f"{tr(_ctx['lang'], 'boot.err_webview', error=e)}\n\n{_log_dir()}",
        )
        sys.exit(1)


def _main_inner():
    # 前回のプロセスが残した WebView2 データフォルダ(run-<pid>)のうち、動いていないものを片付ける
    removed = startup.cleanup_stale_userdata()
    if removed:
        logger.info(f"古い WebView2 データフォルダを削除しました: {', '.join(removed)}")

    # ウィンドウを先に作り、スプラッシュを見せる。題名は二重起動時の前面化が探すため固定
    window = webview.create_window(
        title=MAIN_TITLE,
        html=startup.build_splash_html(_ctx["lang"]),
        js_api=Api(),
        width=1200,
        height=800,
        min_size=(900, 600),
        resizable=True,
        confirm_close=True,
    )
    _ctx["window"] = window
    window.events.closed += on_closed

    # アプリアイコン（EXEに埋め込み済みのアイコンがWindowsでは既定で使われるが、
    # pywebviewのwinforms実装はicon=が指定されていればそちらを優先するため念のため明示する）
    icon_path = str(get_app_dir() / "assets" / "icon.ico")

    # GUIループ開始（boot はGUIが動いたあとに別スレッドで実行される）
    from src_backends import single_instance

    try:
        webview.start(
            boot,
            debug=False,
            private_mode=True,
            storage_path=str(startup.userdata_dir()),
            icon=icon_path if os.path.isfile(icon_path) else None,
        )
    finally:
        # 終了処理は遅いことがあるため、先にミューテックスを解放する
        # （すぐの再起動が二重起動と判定されて無言で終わらないように）
        single_instance.release()
        _boot_done.wait(5)  # 起動処理の途中で閉じられたとき、起動し終えたサーバーも確実に止める
        stop_server()
        app_logging.note_known_fatal_codes()
        shutdown_children()


if __name__ == "__main__":
    main()
