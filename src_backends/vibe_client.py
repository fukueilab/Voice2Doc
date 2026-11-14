"""vibe-server 音声認識クライアントモジュール

vibe-serverはwhisper.cppベースのローカル音声認識サーバー。
OpenAI互換HTTP APIを提供する。
"""
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import is_valid_vibe_model
from .config import normalize_model_name as _normalize_model_name
from .i18n import t
from .path_utils import app_data_dir, get_cache_base_dir

logger = logging.getLogger("voice2doc.vibe")

# vibe-server.exe検出パス。vibe (github.com/thewh1teagle/vibe) がTauriサイドカーとして
# インストール先に置くサーバーバイナリを使う（アプリには同梱しない。
# 利用者が Vibe をインストールする前提。ffmpeg.exe も同じフォルダにある）
VIBE_RELEASES_URL = "https://github.com/thewh1teagle/vibe/releases"
VIBE_DIR = Path(os.path.expanduser("~")) / "AppData" / "Local" / "vibe"
VIBE_PROGRAMS_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "vibe"
VIBE_BINARY_CANDIDATES = [
    VIBE_DIR / "vibe-server.exe",
    VIBE_PROGRAMS_DIR / "vibe-server.exe",
    Path("vibe-server.exe"),
]

# モデルURL (ggml-large-v3-turbo)
DEFAULT_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
DEFAULT_MODEL_NAME = "large-v3-turbo"

# 認識時のヒント(whisperの初期プロンプト)。句読点付きの例文を与えると句読点付きで出力されやすくなる
TRANSCRIBE_PROMPT_JA = "こんにちは。今日は、会議の内容についてお話しします。よろしくお願いします。"

# vibe-serverプロセス管理
_vibe_process: Optional[subprocess.Popen] = None
_vibe_port: Optional[int] = None
_vibe_model_name: Optional[str] = None


_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


def _find_vibe_install_dir_from_registry() -> Optional[Path]:
    """vibeインストーラーがHKCUに書き込むアンインストール情報からインストール先を取得する。
    %LOCALAPPDATA%\\vibe 等の固定パス推測だけに頼らない検出経路（レジストリ未登録・読み取り
    失敗時はNoneを返し、呼び出し元は固定パス候補にフォールバックする）"""
    try:
        import winreg
    except ImportError:
        return None

    key_path = _UNINSTALL_KEY + r"\vibe"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
    except OSError:
        return None

    if not install_location:
        return None
    # インストーラーが値を囲みの二重引用符付きで書き込む場合があるため除去する
    install_location = install_location.strip('"')
    install_dir = Path(install_location)
    return install_dir if install_dir.is_dir() else None


def _find_vibe_install_dirs_from_hklm() -> list[Path]:
    """全ユーザー向けにインストールされた場合のアンインストール情報（HKLM、32/64ビット両方）から
    インストール先を取得する。読み取りに失敗した場合は空のリスト"""
    try:
        import winreg
    except ImportError:
        return []

    dirs: list[Path] = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_KEY + r"\vibe", 0, winreg.KEY_READ | view
            ) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        location = (location or "").strip().strip('"')
        if location and Path(location).is_dir() and Path(location) not in dirs:
            dirs.append(Path(location))
    return dirs


def _program_files_candidates() -> list[Path]:
    """Program Files / Program Files (x86) 配下の vibe フォルダの候補"""
    result: list[Path] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            candidate = Path(base) / "vibe" / "vibe-server.exe"
            if candidate not in result:
                result.append(candidate)
    return result


def _start_menu_shortcut_targets() -> list[Path]:  # 重い（PowerShell起動）ので deep 検出のときだけ呼ぶ
    """スタートメニューの Vibe のショートカット（.lnk）の参照先フォルダにある vibe-server.exe の候補。
    PowerShell（WScript.Shell）で参照先を解決する。失敗しても検出全体は続行する"""
    if os.name != "nt":
        return []
    roots = [
        Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for base in (os.environ.get("APPDATA"), os.environ.get("ProgramData"))
        if base  # 未設定だと相対パス（作業フォルダ）を調べてしまうので除く
    ]
    links: list[Path] = []
    for root in roots:
        try:
            if root.is_dir():
                links.extend(p for p in root.rglob("*.lnk") if "vibe" in p.stem.lower())
        except OSError:
            continue
    if not links:
        return []

    # パスはスクリプトへ埋め込まず、環境変数で渡す（引用符の問題を避ける）
    script = (
        "$s=New-Object -ComObject WScript.Shell;"
        r"foreach($p in $env:V2D_LNKS -split '\|'){if($p){try{$s.CreateShortcut($p).TargetPath}catch{}}}"
    )
    env = dict(os.environ, V2D_LNKS="|".join(str(p) for p in links[:10]))
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=8, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"スタートメニューのショートカット解決に失敗: {e}")
        return []

    result: list[Path] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        candidate = Path(line).parent / "vibe-server.exe"
        if candidate not in result:
            result.append(candidate)
    return result


def _extra_candidates(deep: bool = False) -> list[tuple[str, Path]]:
    """固定パスで見つからなかったときに調べる追加の場所（説明, vibe-server.exeのパス）の一覧"""
    extras: list[tuple[str, Path]] = []
    hkcu = _find_vibe_install_dir_from_registry()
    if hkcu:
        extras.append(("registry(HKCU)", hkcu / "vibe-server.exe"))
    for d in _find_vibe_install_dirs_from_hklm():
        extras.append(("registry(HKLM)", d / "vibe-server.exe"))
    for c in _program_files_candidates():
        extras.append(("Program Files", c))
    if deep:
        for c in _start_menu_shortcut_targets():
            extras.append(("Start menu shortcut", c))
    return extras


def detect_vibe_binary(custom_path: str = "", deep: bool = False) -> tuple[Optional[Path], list[str]]:
    """vibe-server.exeを探し、(見つかったパス or None, 調べた場所の一覧) を返す。
    順序: 設定のパス → 固定パス → レジストリ(HKCU/HKLM)・Program Files・(deep のとき)スタートメニュー。
    deep=False は処理のたびに呼ばれる経路向けで、時間のかかるスタートメニューの調査をしない"""
    searched: list[str] = []

    if custom_path:
        p = Path(custom_path)
        searched.append(f"settings: {custom_path}")
        if p.exists():
            return p, searched
        logger.warning(f"指定されたvibe-server.exeが見つかりません: {custom_path}")

    for candidate in VIBE_BINARY_CANDIDATES:
        searched.append(str(candidate))
        if candidate.exists():
            logger.info(f"vibe-server.exeを検出: {candidate}")
            return candidate, searched

    for label, candidate in _extra_candidates(deep):
        searched.append(f"{label}: {candidate}")
        if candidate.exists():
            logger.info(f"vibe-server.exeを検出（{label}）: {candidate}")
            return candidate, searched

    logger.warning(f"vibe-server.exeが見つかりません。調べた場所: {searched}")
    return None, searched


def find_vibe_binary(custom_path: str = "") -> Optional[Path]:
    """vibe-server.exeのパスを検出"""
    return detect_vibe_binary(custom_path)[0]


# whisper.cpp(ggml)モデルの完成時のバイト数。Vibeがダウンロード中の未完成ファイルを
# 完成扱いにしないための下限（未知のモデルは一時ファイルの有無だけで判断する）
_MODEL_EXPECTED_BYTES = {
    "tiny": 77_691_713,
    "base": 147_951_465,
    "small": 487_601_967,
    "medium": 1_533_763_059,
    "large-v3": 3_095_033_483,
    "large-v3-turbo": 1_624_555_275,
}
_PARTIAL_SUFFIXES = (".part", ".partial", ".tmp", ".download", ".crdownload", ".aria2")


def model_search_dirs(vibe_bin_dir: Optional[Path] = None) -> list[Path]:
    """モデルファイルを探すフォルダの一覧（start_vibe()/ensure_model()/model_status()で共通）"""
    dirs: list[Path] = []
    if vibe_bin_dir is not None:
        dirs.append(Path(vibe_bin_dir))  # vibe-server.exeと同じディレクトリ
    dirs.extend([
        get_cache_base_dir() / "vibe",  # キャッシュディレクトリ
        app_data_dir(Path(os.path.expanduser("~")) / "Documents") / ".cache" / "vibe",  # 開発環境のキャッシュ
        Path(os.environ.get("LOCALAPPDATA", "")) / "github.com.thewh1teagle.vibe",
        Path(os.environ.get("APPDATA", "")) / "github.com.thewh1teagle.vibe",
        Path(os.environ.get("APPDATA", "")) / "aisite" / "models",
    ])
    return dirs


def _model_file_complete(path: Path, model_name: str) -> bool:
    """モデルのファイルが完成しているか（サイズ不足・空・一時ファイルが隣にある場合は未完成）"""
    try:
        size = path.stat().st_size
        expected = _MODEL_EXPECTED_BYTES.get(model_name)
        if size == 0 or (expected and size < expected * 0.98):
            return False
        return not any(p.suffix.lower() in _PARTIAL_SUFFIXES for p in path.parent.glob(path.name + ".*"))
    except OSError:
        return False


# 設定のモデルが使えないとき、代わりに使うモデルの優先順（取得済みのものだけ）
FALLBACK_MODEL_ORDER = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")


def find_model_file(model_name: str, vibe_bin_dir: Optional[Path] = None) -> Optional[Path]:
    """完成したモデルファイル（ggml-<名前>.bin）を候補フォルダから探す。無い・未完成なら None"""
    if not is_valid_vibe_model(model_name):
        return None
    filename = f"ggml-{_normalize_model_name(model_name)}.bin"
    for d in model_search_dirs(vibe_bin_dir):
        candidate = d / filename
        try:
            if candidate.is_file() and _model_file_complete(candidate, _normalize_model_name(model_name)):
                return candidate
        except OSError:
            continue
    return None


def ready_models(vibe_bin_dir: Optional[Path] = None, exclude: str = "") -> list[str]:
    """取得済み（完成）のモデル名を、代わりに使う優先順で返す。exclude のモデルは除く"""
    return [m for m in FALLBACK_MODEL_ORDER
            if m != _normalize_model_name(exclude) and find_model_file(m, vibe_bin_dir) is not None]


def model_status(model_name: str, vibe_bin_dir: Optional[Path] = None) -> str:
    """モデルの状態を返す: "ready"（完成） / "downloading"（未完成・ダウンロード中の疑い） / "missing"（無い）。
    Vibeがダウンロード中の未完成ファイル（サイズ不足・一時ファイルが隣にある）は "ready" にしない"""
    model_name = _normalize_model_name(model_name)
    if not is_valid_vibe_model(model_name):
        return "missing"
    filename = f"ggml-{model_name}.bin"
    expected = _MODEL_EXPECTED_BYTES.get(model_name)
    incomplete = False
    for d in model_search_dirs(vibe_bin_dir):
        try:
            candidate = d / filename
            partials = []
            if d.is_dir():
                partials = [p for p in d.glob(filename + ".*") if p.suffix.lower() in _PARTIAL_SUFFIXES]
            if candidate.is_file():
                size = candidate.stat().st_size
                if partials or (expected and size < expected * 0.98) or size == 0:
                    incomplete = True
                    continue
                return "ready"
            if partials:
                incomplete = True
        except OSError:
            continue
    return "downloading" if incomplete else "missing"


def configured_model_name() -> str:
    """設定の文字起こしモデル（読めなければ既定）"""
    try:
        from .config import load_config

        return load_config().vibe_model or DEFAULT_MODEL_NAME
    except Exception:  # noqa: BLE001
        return DEFAULT_MODEL_NAME


def configured_binary_path() -> str:
    """設定の「vibe-server.exeのパス」（未設定・読めなければ空＝自動検出）"""
    try:
        from .config import load_config

        return load_config().vibe_server_path or ""
    except Exception:  # noqa: BLE001 - 設定が読めなくても自動検出で続ける
        return ""


def find_ffmpeg(binary_path: str = "") -> Optional[Path]:
    """ffmpeg.exe を探す。Vibe のフォルダ（vibe-server.exe と同じ場所）→ PATH の順。見つからなければ None"""
    vibe_bin = find_vibe_binary(binary_path)
    if vibe_bin is not None:
        candidate = Path(vibe_bin).parent / "ffmpeg.exe"
        if candidate.is_file():
            return candidate
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def add_vibe_to_path(binary_path: str = "") -> bool:
    """Vibe のフォルダに ffmpeg.exe があれば PATH の先頭に加える（openai-whisper は内部で ffmpeg コマンドを呼ぶため）。
    加えた（既に入っている場合も含む）なら True"""
    vibe_bin = find_vibe_binary(binary_path)
    if vibe_bin is None or not (Path(vibe_bin).parent / "ffmpeg.exe").is_file():
        return False
    vibe_dir = str(Path(vibe_bin).parent.resolve())
    current = os.environ.get("PATH", "")
    if vibe_dir.lower() not in [p.lower() for p in current.split(os.pathsep)]:
        os.environ["PATH"] = vibe_dir + os.pathsep + current if current else vibe_dir
        logger.info(f"Vibe のフォルダを PATH に追加しました（ffmpeg.exe 用）: {vibe_dir}")
    return True


def is_vibe_running() -> bool:
    """vibe-serverプロセスが稼働中か確認"""
    global _vibe_process, _vibe_port
    if _vibe_process is None or _vibe_port is None:
        logger.debug(f"is_vibe_running: False (process={_vibe_process}, port={_vibe_port})")
        return False
    poll = _vibe_process.poll()
    if poll is not None:
        logger.warning(f"vibe-serverプロセスが終了しました (code={poll})")
        _vibe_process = None
        _vibe_port = None
        return False
    logger.debug(f"is_vibe_running: True (pid={_vibe_process.pid}, port={_vibe_port})")
    return True


def get_vibe_port() -> Optional[int]:
    """現在のvibe-serverポート番号を取得"""
    return _vibe_port


def running_model_name() -> Optional[str]:
    """稼働中の vibe-server のモデル名（稼働していない・不明なら None）"""
    return _vibe_model_name if is_vibe_running() else None


def start_vibe(
    model_name: str = DEFAULT_MODEL_NAME,
    binary_path: str = "",
) -> int:
    """vibe-serverを起動し、ポート番号を返す

    Args:
        model_name: 使用するモデル名 (例: large-v3-turbo)
        binary_path: vibe-server.exeのパス（空なら自動検出）

    Returns:
        int: 接続ポート番号

    Raises:
        RuntimeError: vibe-server.exeが見つからないか、起動に失敗
    """
    global _vibe_process, _vibe_port, _vibe_model_name

    if is_vibe_running() and model_name and _vibe_model_name and \
            _normalize_model_name(_vibe_model_name) != _normalize_model_name(model_name):
        # 設定で文字起こしモデルが変わった。モデルは起動時にしか指定できないので、止めて新しいモデルで起動し直す
        if find_model_file(model_name, find_vibe_binary_dir()) is None:
            # 新しいモデルが無いのに止めない（動いているサーバーをそのまま使えるように）
            raise RuntimeError(t("vibe.model_file_missing", model=model_name))
        logger.info(f"文字起こしモデルが変わったため vibe-server を再起動します: {_vibe_model_name} -> {model_name}")
        stop_vibe()

    if is_vibe_running():
        logger.info(f"vibe-serverは既に稼働中 (port={_vibe_port})")
        return _vibe_port

    vibe_bin = find_vibe_binary(binary_path)
    if vibe_bin is None:
        raise RuntimeError(t("vibe.server_missing"))

    logger.info(f"vibe-server起動中: {vibe_bin}")

    # モデルパスを決定（複数の候補ディレクトリから探す）
    model_arg = ""
    if model_name:
        vibe_bin_dir = Path(vibe_bin).parent
        # 候補となるモデルディレクトリ
        candidate_dirs = model_search_dirs(vibe_bin_dir)

        # 未完成のファイルは使わない
        model_file = find_model_file(model_name, vibe_bin_dir)

        if model_file:
            model_arg = str(model_file)
            logger.info(f"文字起こしモデルファイル: {model_arg} ({model_file.stat().st_size / 1024 / 1024:.1f} MB)")
        else:
            # vibe-server は名前ではなくファイルのパスを要求し、無ければ読めずに終了する。
            # 取得（ダウンロード）は呼び出し側の責任なので、ここでは分かりやすい例外にする
            logger.warning(f"文字起こしモデルファイルが見つかりません (検索パス: {[str(d) for d in candidate_dirs]})")
            raise RuntimeError(t("vibe.model_file_missing", model=model_name))

    cmd = [str(vibe_bin), "serve", "--port", "0"]
    if model_arg:
        cmd.append(model_arg)

    logger.info(f"vibe-serverコマンド: {' '.join(cmd)}")

    try:
        _vibe_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info(f"vibe-serverプロセス起動: PID={_vibe_process.pid}")
    except OSError as e:
        raise RuntimeError(t("vibe.start_failed", error=e))

    # stderrを別スレッドで読み込み（バッファオーバーフロー防止）
    stderr_lines = []
    def read_stderr():
        try:
            for line in _vibe_process.stderr:
                stderr_lines.append(line.strip())
                logger.info(f"vibe-server stderr: {line.strip()}")
        except Exception:
            pass

    import threading
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    # stdoutから {"status":"ready","port":XXXXX} を待機（最大60秒）
    start_time = time.time()
    timeout = 60

    while time.time() - start_time < timeout:
        if _vibe_process.poll() is not None:
            stderr_thread.join(timeout=2)
            stderr_text = "\n".join(stderr_lines) if stderr_lines else "(no stderr)"
            raise RuntimeError(t("vibe.crashed", code=_vibe_process.returncode, stderr=stderr_text))

        line = _vibe_process.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue

        line = line.strip()
        logger.info(f"vibe-server stdout: {line}")

        try:
            data = json.loads(line)
            if data.get("status") == "ready" and "port" in data:
                _vibe_port = data["port"]
                _vibe_model_name = model_name
                logger.info(f"vibe-server起動完了 (port={_vibe_port})")

                # stdoutドラインスレッド（pipeバッファオーバーフロー防止）
                def drain_stdout():
                    try:
                        for line in _vibe_process.stdout:
                            logger.debug(f"vibe-server stdout (drain): {line.strip()}")
                    except Exception:
                        pass
                stdout_drain = threading.Thread(target=drain_stdout, daemon=True)
                stdout_drain.start()

                return _vibe_port
        except json.JSONDecodeError:
            continue

    # タイムアウト
    _vibe_process.kill()
    _vibe_process = None
    raise RuntimeError(t("vibe.start_timeout"))


def stop_vibe():
    """vibe-serverを停止"""
    global _vibe_process, _vibe_port, _vibe_model_name

    if _vibe_process is not None:
        logger.info("vibe-server停止中...")
        try:
            _vibe_process.terminate()
            _vibe_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("vibe-server強制終了")
            _vibe_process.kill()
        except Exception as e:
            logger.warning(f"vibe-server停止エラー: {e}")
        finally:
            _vibe_process = None
            _vibe_port = None
            _vibe_model_name = None
            logger.info("vibe-server停止完了")


def ensure_model(model_url: str = "", progress_callback=None, require_server: bool = True) -> bool:
    """モデルの存在確認（ファイルシステムで確認、pullコマンド不使用）

    Args:
        model_url: モデルのダウンロードURL（空ならデフォルト）
        progress_callback: 進捗コールバック (message, progress=None)
        require_server: Trueならvibe-server稼働チェック実行（デフォルトTrue）

    Returns:
        bool: モデルが利用可能かどうか
    """
    global _vibe_model_name

    if require_server and not is_vibe_running():
        logger.warning("vibe-serverが稼働していません")
        return False

    url = model_url or DEFAULT_MODEL_URL
    model_filename = url.split("/")[-1]  # 例: "ggml-large-v3-turbo.bin"
    model_name = model_filename.replace(".bin", "")  # 例: "ggml-large-v3-turbo"

    if progress_callback:
        progress_callback(f"文字起こしモデル '{model_name}' の存在確認中...")

    # モデルファイルの存在確認（複数の候補ディレクトリから探す）
    vibe_bin = find_vibe_binary()
    if vibe_bin is None:
        return False

    vibe_bin_dir = Path(vibe_bin).parent
    candidate_dirs = model_search_dirs(vibe_bin_dir)

    model_file = None
    for d in candidate_dirs:
        candidate = d / model_filename  # 例: "ggml-large-v3-turbo.bin"
        if candidate.exists():
            model_file = candidate
            break

    if model_file:
        _vibe_model_name = model_name
        if progress_callback:
            progress_callback(f"文字起こしモデル '{model_name}' の準備完了")
        logger.info(f"文字起こしモデル準備完了: {model_name} ({model_file})")
        return True
    else:
        logger.warning(f"文字起こしモデルファイルが見つかりません (検索パス: {[str(d) for d in candidate_dirs]})")
        if progress_callback:
            progress_callback(t("vibe.model_missing", model=model_name))
        return False


def find_vibe_binary_dir() -> Optional[Path]:
    """vibe-server.exe のあるフォルダ（モデルの候補フォルダにも含まれる）。見つからなければ None"""
    binary = find_vibe_binary(configured_binary_path())
    return Path(binary).parent if binary else None


def ensure_model_downloaded(model_name: str, progress_callback=None) -> Path:
    """指定された文字起こしモデルがローカルに無ければHuggingFaceからダウンロードする。

    whisper.cpp形式のGGMLモデルはサイズ違いで6種類以上あり、setup_checker.pyの
    _download_and_verify()のようにモデルごとに固定SHA256ハッシュを維持するのは
    上流（ggerganov/whisper.cpp のHuggingFaceリポジトリ）の更新を追従するコストが
    高すぎるため、ハッシュ検証は行わない。代わりにContent-Lengthとダウンロード後の
    実バイト数を比較するサイズチェックのみ行い、一時ディレクトリも毎回一意にすることで
    TOCTOU（差し替え）リスクとダウンロード破損を軽減する。

    Returns:
        Path: ダウンロード済み（または既存）のモデルファイルパス
    Raises:
        RuntimeError: ダウンロードに失敗、またはサイズが一致しない場合
    """
    import shutil
    import tempfile
    import urllib.request

    model_name = _normalize_model_name(model_name)
    if not is_valid_vibe_model(model_name):
        raise RuntimeError(t("vibe.model_dl_failed_err", model=model_name, error="invalid model name"))
    model_filename = f"ggml-{model_name}.bin"

    # 既存ファイルを探す（start_vibe()/model_status()と同じ候補フォルダ。未完成のファイルは取得済みと見なさない）
    existing = find_model_file(model_name, find_vibe_binary_dir())
    if existing is not None:
        logger.info(f"モデル '{model_name}' は既にダウンロード済み: {existing}")
        return existing

    dest_dir = get_cache_base_dir() / "vibe"
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / model_filename

    url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{model_filename}"
    temp_dir = Path(tempfile.mkdtemp(prefix="voice2doc_vibe_model_"))
    temp_path = temp_dir / model_filename

    try:
        logger.info(f"モデルダウンロード開始: {url}")
        if progress_callback:
            progress_callback(f"モデル '{model_name}' をダウンロード中...", 0)

        with urllib.request.urlopen(url, timeout=30) as resp:
            expected_size = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(temp_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and expected_size:
                        progress_callback(
                            f"モデル '{model_name}' をダウンロード中...",
                            int(downloaded * 100 / expected_size),
                        )

        if expected_size and downloaded != expected_size:
            raise RuntimeError(t("vibe.size_mismatch", expected=expected_size, actual=downloaded))

        shutil.move(str(temp_path), str(final_path))
        logger.info(f"モデルダウンロード完了: {final_path} ({downloaded / 1024 / 1024:.1f} MB)")
        if progress_callback:
            progress_callback(f"モデル '{model_name}' の準備完了", 100)
        return final_path

    except Exception as e:
        logger.error(f"モデルダウンロードエラー: {e}")
        if progress_callback:
            progress_callback(t("vibe.model_dl_failed", model=model_name), 0)
        raise RuntimeError(t("vibe.model_dl_failed_err", model=model_name, error=e)) from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def transcribe(
    audio_path: str | Path,
    model_name: str = DEFAULT_MODEL_NAME,
    language: Optional[str] = "ja",
    progress_callback=None,
) -> dict:
    """vibe-server APIを使って音声/動画ファイルを文字起こし

    Args:
        audio_path: 音声/動画ファイルのパス
        model_name: モデル名
        language: 言語コード (None=自動検出)
        progress_callback: 進捗コールバック (step, message, progress=None)

    Returns:
        dict: {"text": str, "segments": list, "language": str}
    """
    from .whisper_processor import extract_audio_from_video, get_audio_duration, is_video_file

    file_path = Path(audio_path)
    if not file_path.exists():
        raise FileNotFoundError(t("rec.file_not_found", path=file_path))

    if not is_vibe_running():
        logger.warning("vibe-serverが停止しています。再起動を試みます...")
        try:
            # 停止すると稼働中のモデル名が消えるため、先に控える。不明なら設定のモデルで起動し直す
            restart_model = _vibe_model_name or configured_model_name()
            stop_vibe()
            start_vibe(model_name=restart_model)
        except Exception as e:
            raise RuntimeError(t("vibe.restart_failed", error=e))
        if not is_vibe_running():
            raise RuntimeError(t("vibe.not_running"))

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

        # vibe-server API呼び出し
        if progress_callback:
            progress_callback("transcribe", t("rec.transcribing"), 0)

        logger.info(f"vibe-server文字起こし開始: {file_path.name}")

        url = f"http://127.0.0.1:{_vibe_port}/v1/audio/transcriptions"

        with open(audio_to_process, "rb") as f:
            files = {"file": (file_path.name, f, "audio/wav")}
            data = {}
            if language:
                data["language"] = language
            # 日本語は句読点入りの例文をヒント(初期プロンプト)として渡し、句読点付きの出力を誘導する
            send_prompt = language == "ja"
            if send_prompt:
                data["prompt"] = TRANSCRIBE_PROMPT_JA

            try:
                try:
                    response = httpx.post(url, files=files, data=data, timeout=600.0)
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    # promptを受け付けないバージョンの場合は、ヒント無しで1回だけやり直す
                    if send_prompt and e.response.status_code in (400, 422):
                        logger.warning("vibe-serverがpromptを受け付けませんでした。ヒント無しで再試行します")
                        f.seek(0)
                        data.pop("prompt", None)
                        response = httpx.post(url, files=files, data=data, timeout=600.0)
                        response.raise_for_status()
                    else:
                        raise
            except httpx.TimeoutException:
                raise RuntimeError(t("vibe.api_timeout"))
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    raise RuntimeError(t("vibe.busy"))
                raise RuntimeError(t("vibe.api_error", status=e.response.status_code, text=e.response.text))

        result = response.json()
        # vibe-serverは認識セグメントごとに\n\nで区切って返すため、余分な空行を除去し1行1セグメントに正規化する
        text = re.sub(r"\n{2,}", "\n", result.get("text", "")).strip()
        # 無音などでヒント文がそのまま出力されることがあるため除去する
        if send_prompt and TRANSCRIBE_PROMPT_JA in text:
            text = text.replace(TRANSCRIBE_PROMPT_JA, "").strip()

        # セグメント情報を構築（vibe-serverはセグメントを返さない場合があるため空リスト）
        segments = []

        # 進捗を100%に設定
        if progress_callback:
            progress_callback("transcribe", t("rec.transcribed"), 100)

        logger.info(f"vibe-server文字起こし完了: {file_path.name} ({len(text)}文字)")

        return {
            "text": text,
            "segments": segments,
            "language": language or "unknown",
        }

    finally:
        # 一時ファイルを削除
        if temp_audio and temp_audio.exists():
            temp_audio.unlink()
            logger.info(f"一時ファイル削除: {temp_audio.name}")

