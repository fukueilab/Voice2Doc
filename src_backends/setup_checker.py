"""前提ソフトウェア自動インストールモジュール"""
import os
import sys
import json
import subprocess
import logging
import hashlib
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Callable

from .path_utils import get_app_dir, is_frozen
from .config import CACHE_DIR

logger = logging.getLogger("voice2doc.setup")

# GPU検出結果のキャッシュファイル名。GPU構成は起動間で変わることがほぼ無い一方、
# 検出にはPowerShellサブプロセス起動（最大10秒のタイムアウト）を伴うため、
# 一度検出できた結果はディスクにキャッシュし毎回の起動での再実行を避ける。
# CACHE_DIRを都度参照する形にしているのは、テストで`setup_checker.CACHE_DIR`を
# 差し替えるだけで隔離できるようにするため（モジュール読み込み時に一度だけ計算すると
# テストのmonkeypatchが効かない）。
GPU_CACHE_FILENAME = "gpu_info_cache.json"

# Visual C++ Runtime チェック用DLL
VC_RUNTIME_DLLS = [
    "vcruntime140.dll",
    "msvcp140.dll",
    "vcruntime140_1.dll",
]

# VC_REDIST_URL/WEBVIEW2_INSTALLER_URL は"最新版"を指すMicrosoft公式のリダイレクトURL（aka.ms/go.microsoft.com）
# であり、Microsoft側が配布ファイルを更新すると実体のハッシュ値も変わる。その場合、以下の固定ハッシュ値は
# 古くなり検証が失敗し続ける（インストールは"失敗として安全側に倒れる"だけでクラッシュはしないが、
# 該当ランタイムの自動インストールが機能しなくなる）。新しいハッシュ値に更新する際は、
# installer/voice2doc_installer.nsi の VC_REDIST_HASH / WEBVIEW2_HASH も必ず同時に更新すること。

# VS 2015-2022 Redistributable ダウンロードURL・SHA256ハッシュ
# (installer/voice2doc_installer.nsi の VC_REDIST_HASH と同一の値を使用)
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_REDIST_SHA256 = "CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B"

# WebView2 Runtime インストーラーURL・SHA256ハッシュ
# (installer/voice2doc_installer.nsi の WEBVIEW2_HASH と同一の値を使用)
WEBVIEW2_INSTALLER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
WEBVIEW2_SHA256 = "8C4A80540B6BBCBEF30A4E8C7D1AC504B6FC09DB922B4ACDFD85C9D5F6F1050E"


def _sha256_of(path: Path) -> str:
    """ファイルのSHA256ハッシュ値を16進大文字文字列で返す"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _download_and_verify(
    url: str,
    filename: str,
    expected_sha256: str,
    progress_callback: Optional[Callable] = None,
    label: str = "インストーラー",
) -> Optional[Path]:
    """
    URLからファイルを一意な一時ディレクトリにダウンロードし、SHA256ハッシュを検証する。

    Windowsの%TEMP%は同一ユーザーの他プロセスから書き込み可能なため、固定ファイル名だと
    ダウンロード完了から実行までの間にファイルをすり替えられるリスク（TOCTOU）がある。
    毎回ランダムな一時ディレクトリを作ることでファイルパスを予測不能にし、
    さらにSHA256ハッシュ検証でダウンロード内容の改ざん・破損を検出する。

    Returns:
        検証済みファイルのPath。ダウンロード失敗・ハッシュ不一致の場合はNone。
    """
    import urllib.request

    # DOWNLOAD_TIMEOUT_SEC: 接続確立・各読み取り操作それぞれに適用されるソケットタイムアウト。
    # urlretrieve()にはタイムアウト指定がなく、応答のないサーバーに対して無期限にハングしうる
    # （この関数はensure_all_prerequisites()経由でuvicorn起動前に同期的に呼ばれるため、
    # ハングするとアプリがまったく起動しなくなる）。urlopen+チャンク書き込みに置き換えて対処する
    DOWNLOAD_TIMEOUT_SEC = 30

    temp_dir = Path(tempfile.mkdtemp(prefix="voice2doc_"))
    dest_path = temp_dir / filename

    try:
        if progress_callback:
            progress_callback(f"{label}をダウンロード中...")
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SEC) as resp, open(dest_path, "wb") as f:
            shutil.copyfileobj(resp, f)

        if progress_callback:
            progress_callback(f"{label}のハッシュ値を検証中...")
        actual_sha256 = _sha256_of(dest_path)
        if actual_sha256 != expected_sha256:
            logger.error(
                f"{label}のハッシュ値が一致しません（改ざん・破損の可能性）: "
                f"expected={expected_sha256}, actual={actual_sha256}"
            )
            if progress_callback:
                progress_callback(f"{label}のハッシュ検証に失敗しました")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None

        logger.info(f"{label}のハッシュ検証OK")
        return dest_path

    except Exception as e:
        logger.error(f"{label}のダウンロードエラー: {e}")
        if progress_callback:
            progress_callback(f"{label}のダウンロードに失敗しました")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None


def check_vc_runtime() -> bool:
    """Visual C++ Runtime がインストールされているかチェック"""
    # GetModuleHandleW は未ロードのDLLに対して例外を送出せず戻り値0を返すだけなので、
    # 例外ハンドリングに頼らずSystem32配下のDLLファイル存在を直接確認する
    # (SYSTEMROOTが空文字列の場合にos.environ.getのデフォルト値が使われないため、orで明示的にフォールバック)
    system32 = Path(os.environ.get("SYSTEMROOT") or "C:\\Windows") / "System32"
    app_dir = get_app_dir()
    for dll in VC_RUNTIME_DLLS:
        # PyInstallerでvcruntime140.dll等がexeと同じディレクトリに同梱される場合があるため、
        # System32になくてもアプリ同梱DLLがあれば実行に支障はない
        if not (system32 / dll).exists() and not (app_dir / dll).exists():
            logger.info(f"Visual C++ Runtime が見つかりません: {dll}")
            return False
    logger.info("Visual C++ Runtime: OK")
    return True


def check_webview2() -> bool:
    """Edge WebView2 Runtime がインストールされているかチェック"""
    # レジストリで確認 (HKLM)
    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "pv")
            if value:
                logger.info(f"WebView2 Runtime: OK (バージョン: {value})")
                return True
    except (FileNotFoundError, OSError):
        pass

    # HKCU チェック（ユーザーインストール）
    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "pv")
            if value:
                logger.info(f"WebView2 Runtime: OK (HKCU, バージョン: {value})")
                return True
    except (FileNotFoundError, OSError):
        pass

    # ファイルシステムでも確認
    edge_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "EdgeWebView",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "EdgeWebView",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "EdgeWebView",
    ]
    for path in edge_paths:
        if path.exists():
            logger.info(f"WebView2 Runtime: OK (ファイルシステム検出: {path})")
            return True

    logger.info("WebView2 Runtime: 未インストール")
    return False


def check_ffmpeg() -> bool:
    """FFmpeg が利用可能かチェック（同梱しない。Vibe のフォルダの ffmpeg.exe、無ければ PATH の ffmpeg）"""
    from . import vibe_client

    path = vibe_client.find_ffmpeg(vibe_client.configured_binary_path())
    if path is not None:
        logger.info(f"FFmpeg: OK ({path})")
        return True
    logger.info("FFmpeg: 未インストール（Vibe をインストールすると ffmpeg.exe も入ります）")
    return False


def check_vibe() -> bool:
    """vibe-server.exeが利用可能かチェック（vibe_client.find_vibe_binary()と同じ検出ロジックに統一）"""
    from . import vibe_client

    binary = vibe_client.detect_vibe_binary(vibe_client.configured_binary_path(), deep=True)[0]
    if binary:
        logger.info(f"vibe-server: OK ({binary})")
        return True
    logger.info("vibe-server: 未インストール")
    return False


def check_disk_space(required_gb: float = 7.0) -> bool:
    """ディスク容量をチェック"""
    try:
        import shutil
        usage = shutil.disk_usage(str(get_app_dir()))
        free_gb = usage.free / (1024 ** 3)
        if free_gb >= required_gb:
            logger.info(f"ディスク容量: OK ({free_gb:.1f}GB 空き)")
            return True
        else:
            logger.warning(f"ディスク容量不足: {free_gb:.1f}GB (必要: {required_gb}GB)")
            return False
    except Exception as e:
        logger.warning(f"ディスク容量チェックエラー: {e}")
        return True  # チェック失敗時は許可


_GPU_CACHE_KEYS = {"has_amd_igpu", "has_nvidia", "has_intel"}


def check_gpu(use_cache: bool = True) -> dict:
    """GPU環境を確認（PowerShellサブプロセスの結果をディスクにキャッシュして再利用する）"""
    cache_file = CACHE_DIR / GPU_CACHE_FILENAME
    if use_cache and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and _GPU_CACHE_KEYS.issubset(cached.keys()):
                logger.info(f"GPU確認結果をキャッシュから読み込み: {cached}")
                return cached
            logger.warning(f"GPUキャッシュの形式が不正なため再検出します: {cached}")
        except Exception as e:
            logger.warning(f"GPUキャッシュ読み込みエラー（再検出します）: {e}")

    gpu_info = {
        "has_amd_igpu": False,
        "has_nvidia": False,
        "has_intel": False,
    }

    try:
        # PowerShell Get-CimInstance を使用（wmic は Windows 11 で非推奨）
        result = subprocess.run(
            ["powershell", "-Command", "Get-CimInstance -ClassName Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.lower()
        if "amd" in output or "radeon" in output:
            gpu_info["has_amd_igpu"] = True
        if "nvidia" in output or "geforce" in output:
            gpu_info["has_nvidia"] = True
        if "intel" in output:
            gpu_info["has_intel"] = True

        # returncode!=0や出力なしの一時的な失敗まで「GPU無し」としてキャッシュに固定してしまうと
        # 手動でキャッシュファイルを消さない限り永久に誤判定が残るため、検出に成功した場合のみ書き込む
        if result.returncode == 0 and output.strip():
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(gpu_info, f)
            except Exception as e:
                logger.warning(f"GPUキャッシュ書き込みエラー: {e}")
        else:
            logger.warning(f"GPU確認コマンドが失敗またはno出力のためキャッシュしません (returncode={result.returncode})")
    except Exception as e:
        logger.warning(f"GPU確認エラー: {e}")

    return gpu_info


def install_vc_runtime(progress_callback: Optional[Callable] = None) -> bool:
    """Visual C++ Redistributable をインストール"""
    if check_vc_runtime():
        return True

    if progress_callback:
        progress_callback("Visual C++ Redistributable をインストール中...")

    installer_path = _download_and_verify(
        VC_REDIST_URL, "vc_redist.x64.exe", VC_REDIST_SHA256,
        progress_callback=progress_callback,
        label="Visual C++ Redistributable",
    )
    if installer_path is None:
        return False

    try:
        # サイレントインストール
        if progress_callback:
            progress_callback("Visual C++ Redistributable をインストール中...")
        result = subprocess.run(
            [str(installer_path), "/install", "/quiet", "/norestart"],
            capture_output=True,
            timeout=300,
        )

        if result.returncode == 0:
            logger.info("Visual C++ Redistributable インストール完了")
            if progress_callback:
                progress_callback("Visual C++ Redistributable インストール完了")
            return True
        else:
            logger.error(f"Visual C++ Redistributable インストールエラー: {result.returncode}")
            return False

    except Exception as e:
        logger.error(f"Visual C++ Redistributable インストールエラー: {e}")
        return False
    finally:
        shutil.rmtree(installer_path.parent, ignore_errors=True)


def install_webview2(progress_callback: Optional[Callable] = None) -> bool:
    """Edge WebView2 Runtime をインストール"""
    if check_webview2():
        return True

    if progress_callback:
        progress_callback("WebView2 Runtime をインストール中...")

    installer_path = _download_and_verify(
        WEBVIEW2_INSTALLER_URL, "MicrosoftEdgeWebview2Setup.exe", WEBVIEW2_SHA256,
        progress_callback=progress_callback,
        label="WebView2 Runtime",
    )
    if installer_path is None:
        return False

    try:
        # サイレントインストール
        if progress_callback:
            progress_callback("WebView2 Runtime をインストール中...")
        result = subprocess.run(
            [str(installer_path), "/silent", "/install"],
            capture_output=True,
            timeout=600,
        )

        stdout_text = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        logger.info(f"WebView2 installer stdout: {stdout_text[:500]}")
        logger.info(f"WebView2 installer stderr: {stderr_text[:500]}")

        if result.returncode == 0:
            logger.info("WebView2 Runtime インストール完了")
            if progress_callback:
                progress_callback("WebView2 Runtime インストール完了")
            return True

        # returncode != 0 でも実際にはインストール済みの場合がある（再起動待ち等）
        # エラーコード 2147747880 (0x800736B8) はWebView2が既にインストール済みの場合に発生
        if result.returncode == 2147747880:
            logger.info(f"WebView2 Runtime: インストール済み確認（エラーコード: {result.returncode}、既にインストール済みの可能性）")
            if check_webview2():
                if progress_callback:
                    progress_callback("WebView2 Runtime インストール完了")
                return True

        # その他のエラーコードでも実際にはインストール済みの場合がある
        if check_webview2():
            logger.info(f"WebView2 Runtime: インストール済み確認（終了コード: {result.returncode}、再起動が必要な場合あり）")
            if progress_callback:
                progress_callback("WebView2 Runtime インストール完了（再起動が必要な場合があります）")
            return True

        logger.error(f"WebView2 Runtime インストールエラー: returncode={result.returncode}, stdout={stdout_text[:200]}, stderr={stderr_text[:200]}")
        return False

    except Exception as e:
        logger.error(f"WebView2 Runtime インストールエラー: {e}")
        return False
    finally:
        shutil.rmtree(installer_path.parent, ignore_errors=True)


def run_all_checks(progress_callback: Optional[Callable] = None) -> dict:
    """全ての前提条件をチェック"""
    results = {
        "vc_runtime": False,
        "webview2": False,
        "ffmpeg": False,
        "vibe": False,
        "disk_space": False,
        "gpu": {},
    }

    if progress_callback:
        progress_callback("前提条件をチェック中...")

    results["vc_runtime"] = check_vc_runtime()
    results["webview2"] = check_webview2()
    results["ffmpeg"] = check_ffmpeg()
    results["vibe"] = check_vibe()
    results["disk_space"] = check_disk_space()
    results["gpu"] = check_gpu()

    return results


def ensure_all_prerequisites(progress_callback: Optional[Callable] = None) -> bool:
    """全ての前提条件を確保（必要ならインストール）"""
    if progress_callback:
        progress_callback("前提条件を確認・設定中...")

    # VC++ Runtime
    if not check_vc_runtime():
        if not install_vc_runtime(progress_callback):
            logger.error("Visual C++ Runtime のインストールに失敗しました")

    # WebView2
    if not check_webview2():
        if not install_webview2(progress_callback):
            logger.error("WebView2 Runtime のインストールに失敗しました")

    # 最終チェック
    results = run_all_checks(progress_callback)

    all_ok = all([
        results["vc_runtime"],
        results["webview2"],
        results["ffmpeg"],
        results["vibe"],
        results["disk_space"],
    ])

    return all_ok
