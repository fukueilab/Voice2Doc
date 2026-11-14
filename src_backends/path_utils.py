"""パス解決ユーティリティ (PyInstaller対応)"""
import os
import sys
from pathlib import Path

APP_DIR_NAME = "Voice2Doc"
# WebView の保存領域（ホーム直下）
WEBVIEW_DIR_NAME = ".voice2doc"


def app_data_dir(parent: Path) -> Path:
    """親フォルダの下のアプリのデータフォルダ（Voice2Doc）"""
    return parent / APP_DIR_NAME


def get_app_dir() -> Path:
    """アプリケーションディレクトリを取得 (PyInstaller対応)"""
    if getattr(sys, 'frozen', False):
        # PyInstaller ビルド後
        return Path(sys._MEIPASS)
    else:
        # 開発環境
        return Path(__file__).parent.parent



def get_cache_base_dir() -> Path:
    """キャッシュベースディレクトリを取得"""
    if getattr(sys, 'frozen', False):
        # 単体アプリ: %LOCALAPPDATA%/Voice2Doc
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return app_data_dir(Path(local_app_data))
        else:
            return app_data_dir(Path.home() / "AppData" / "Local")
    else:
        # 開発環境: Documents/Voice2Doc
        return app_data_dir(Path.home() / "Documents")



def is_frozen() -> bool:
    """PyInstaller ビルド後かどうかを判定"""
    return getattr(sys, 'frozen', False)

