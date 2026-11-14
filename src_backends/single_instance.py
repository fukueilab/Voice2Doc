"""二重起動の防止（Windows 名前付きミューテックス）

起動の最初に ``acquire()`` を呼び、False（既に別プロセスが起動中）なら
``bring_existing_to_front()`` で既存ウィンドウを前面に出して終了する。

- ミューテックスはプロセス終了・クラッシュ時に OS が自動で解放するため、ロックファイルのような残骸が残らない。
- ポート番号に依存しない（他のアプリが 8008 を使っていても誤判定しない）。
- ウィンドウアプリ（PyInstaller ``console=False``）では標準出力が無いので、何も出力しない。
"""
import ctypes
import logging
import time
from typing import Optional

MUTEX_NAME = "Local\\Voice2Doc.SingleInstance"
MAIN_WINDOW_TITLE = "話書き - Voice2Doc"
# 初回設定ウィザードの題名（表示言語ごと。i18n の boot.wizard_title と同じ文字列）
WIZARD_WINDOW_TITLES = ("話書き - 初回設定ウィザード", "Voice2Doc - Setup wizard")

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9

logger = logging.getLogger("voice2doc.single_instance")

# 起動中ずっと保持するハンドル（閉じるとミューテックスが解放される）
_handles: list = []


def _create_mutex(name: str) -> bool:
    """名前付きミューテックスを作成する。新規に作れたら True、既に存在していたら False"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    if not handle:
        raise OSError(f"CreateMutexW に失敗しました: {ctypes.get_last_error()}")
    if already_exists:
        kernel32.CloseHandle(handle)
        return False
    _handles.append(handle)
    return True


def acquire(name: str = MUTEX_NAME) -> bool:
    """このプロセスが唯一の起動であれば True。既に別プロセスが起動中なら False。

    ミューテックスを作れない環境（非Windows等）でも起動は妨げず True を返す。
    """
    try:
        return _create_mutex(name)
    except Exception as e:  # noqa: BLE001 - 防止機構の失敗でアプリを起動不能にしない
        logger.warning(f"二重起動の検査に失敗したため続行します: {e}")
        return True


def release() -> None:
    """保持しているミューテックスを解放する（主にテスト用。通常はプロセス終了時にOSが解放）"""
    if not _handles:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        for handle in _handles:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _handles.clear()


def bring_existing_to_front(user32=None, retries: int = 10, delay: float = 0.5) -> bool:
    """起動済みの話書きウィンドウを前面に出す。見つかって前面化を試みたら True。

    1つ目のプロセスがまだウィンドウを作っていない（起動途中）場合に備え、``retries`` 回まで
    ``delay`` 秒間隔で探し直す。
    """
    if user32 is None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # HWNDが64bit環境で切り詰められないよう型を明示する
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
    for attempt in range(max(retries, 1)):
        for title in (MAIN_WINDOW_TITLE, *WIZARD_WINDOW_TITLES):
            hwnd: Optional[int] = user32.FindWindowW(None, title)
            if hwnd:
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                return True
        if attempt < retries - 1 and delay > 0:
            time.sleep(delay)
    return False
