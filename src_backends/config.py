"""設定管理モジュール"""
import copy
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from . import i18n, prompt_sets
from .path_utils import app_data_dir, get_cache_base_dir, is_frozen

logger = logging.getLogger("voice2doc.config")

# アプリケーションバージョン（Semantic Versioning 2.0.0 のプレリリース表記 例: 1.0.0-beta.3。
# /healthのapp_versionとして返しフロントエンドが表示する）。
# インストーラー側(installer/voice2doc_installer.nsiのAPP_VERSION)は同じ文字列に揃えること
# （test_version.pyが一致を検証する）。タグは v<APP_VERSION>
APP_VERSION = "1.0.0-beta.7"

# 履歴からダウンロードできる形式（先頭が既定値）
DOWNLOAD_FORMATS = ("txt", "docx", "pdf", "md")

# 「AIモデルの指定」欄の既定表示（空欄/この文字列のままなら同梱の既定モデルを使用する）
DEFAULT_LLM_MODEL_LABEL = "gemma4:e2b-qat(llama.cpp)"


# LLM の実行装置の設定値（llm_device）。auto=GPU が使えれば GPU、gpu=GPU を優先、cpu=CPU 固定
LLM_DEVICES = ("auto", "gpu", "cpu")
DEFAULT_LLM_DEVICE = "auto"


def normalize_llm_device(value: Optional[str]) -> str:
    """設定値を LLM_DEVICES のいずれかへ正規化する。不正値・未指定は auto"""
    v = (value or "").strip().lower()
    return v if v in LLM_DEVICES else DEFAULT_LLM_DEVICE


_VIBE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_model_name(name: Optional[str]) -> str:
    """文字起こしモデル名を比べるための正規化（"ggml-large-v3-turbo" と "large-v3-turbo" を同じに扱う）"""
    name = (name or "").strip()
    return name[len("ggml-"):] if name.startswith("ggml-") else name


def is_valid_vibe_model(name: Optional[str]) -> bool:
    """文字起こしモデル名として安全か（ファイル名・ダウンロード先の組み立てに使うため、パス区切りや ".." を許さない）"""
    name = normalize_model_name(name)
    return bool(_VIBE_MODEL_RE.match(name)) and ".." not in name


def normalize_llm_model_path(value: Optional[str]) -> str:
    """「AIモデルの指定」の入力値を設定値へ正規化する。

    - 空欄・既定ラベル（DEFAULT_LLM_MODEL_LABEL）→ ""（既定モデル）
    - それ以外 → 実在する .gguf ファイルのパス。条件を満たさなければ ValueError（日本語メッセージ）
    """
    text = (value or "").strip()
    # エクスプローラーの「パスのコピー」で付く前後の引用符を除去
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    if not text or text == DEFAULT_LLM_MODEL_LABEL:
        return ""
    path = Path(text)
    if path.suffix.lower() != ".gguf":
        raise ValueError(i18n.t("cfg.gguf_required"))
    if not path.is_file():
        raise ValueError(i18n.t("cfg.gguf_missing", path=text))
    # 相対パスだと次回起動時の作業フォルダ次第で見つからなくなるため、絶対パスで保存する
    return str(path.resolve())


# ディレクトリ設定 (PyInstaller対応)
# ユーザードキュメントディレクトリ（常時定義 - frozen/開発両方で利用可能）
USER_DOCS = Path(os.path.expanduser("~")) / "Documents"

if is_frozen():
    # 単体アプリ: %LOCALAPPDATA%/Voice2Doc
    BASE_DIR = get_cache_base_dir()
else:
    # 開発環境: Documents/Voice2Doc
    BASE_DIR = app_data_dir(USER_DOCS)

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / ".cache"
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_CONFIG_FILE = BASE_DIR / "default.json"
# 処理済みファイルを記録するログ（元ファイルはinput/に残したまま移動しない。
# 「処理済みかどうか」はこのログの有無で判定する）
DONE_LOG_FILE = BASE_DIR / "done.log"

# ディレクトリ作成
for d in [INPUT_DIR, OUTPUT_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# 既定の選択中プロンプト（文章校正）の本文。言語別は prompt_sets.default_prompt()
DEFAULT_PROMPT = prompt_sets.default_prompt("ja")


@dataclass
class AppConfig:
    """アプリ設定"""
    # 文字起こしエンジン（常に vibe-server.exe。見つからない場合だけ、実行中に限り openai-whisper へ
    # 切り替える。設定ファイルは書き換えない）
    whisper_backend: str = "vibe"  # "vibe" or "python"

    # Whisper モデル
    whisper_model: str = "turbo"
    whisper_language: str = "ja"

    # vibe-server 設定
    vibe_server_path: str = ""  # 空なら Vibe のインストール先から自動検出
    vibe_model: str = "large-v3-turbo"

    # 初回起動ウィザード
    wizard_completed: bool = False

    # AIモデルの指定: ユーザーがダウンロードした任意の .gguf ファイルのフルパス。
    # 空なら既定モデル（DEFAULT_LLM_MODEL_LABEL）を使用する
    llm_model_path: str = ""

    # LLM の実行装置: "auto"（GPU が使えれば GPU、使えなければ CPU）/ "gpu" / "cpu"。
    # 検証は local_llm.normalize_llm_device と POST /config で行う
    llm_device: str = "auto"

    # 外観
    dark_mode: bool = False

    # 表示言語（"ja" / "en"）。UI・進捗メッセージ・既定プロンプトの言語。音声認識の言語(whisper_language)とは別
    ui_language: str = "ja"

    # 履歴からのダウンロード形式の既定値（DOWNLOAD_FORMATSのいずれか）
    download_format: str = "txt"

    # プロンプト
    prompt: str = DEFAULT_PROMPT

    # プロンプト管理
    active_prompt_name: str = prompt_sets.default_active_prompt_name("ja")
    saved_prompts: dict = None  # {"プロンプト名": {"content": "...", "description": "", "output_dir": "", "include_transcript": false, "hidden": false}}  # hidden=処理メニューに出さない

    # ファイル制限
    max_files: int = 10
    max_total_size_bytes: int = 1024 * 1024 * 1024  # 1GB

    # 対応形式 (音声 + 動画 + テキスト)
    allowed_extensions: tuple = (
        # 音声
        ".wav", ".mp3", ".aac", ".m4a", ".ogg", ".flac",
        # 動画
        ".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
        # テキスト
        ".txt", ".md",
    )

    def __post_init__(self):
        if self.saved_prompts is None:
            self.saved_prompts = prompt_sets.default_saved_prompts(self.ui_language)
            if self.ui_language != "ja" and self.prompt == DEFAULT_PROMPT:
                self.prompt = prompt_sets.default_prompt(self.ui_language)
                self.active_prompt_name = prompt_sets.default_active_prompt_name(self.ui_language)


# config.jsonの内容をメモリキャッシュする。全APIエンドポイントの先頭で
# load_config()が呼ばれディスクI/Oが毎回発生していたため、初回のみディスクから読み込み、
# 以降はsave_config()成功時にのみ更新する。load_config()は呼び出し元ごとに独立したコピーを返す
# （キャッシュへの参照をそのまま渡すと、呼び出し元がsave_config()を呼ばずに返り値をミューテートした際に
# その変更がキャッシュに漏れてしまうため）
_config_cache: Optional["AppConfig"] = None


def load_config() -> AppConfig:
    """設定を読み込み（2回目以降はメモリキャッシュを返す）"""
    global _config_cache
    if _config_cache is not None:
        return copy.deepcopy(_config_cache)

    config = _load_config_from_disk()
    _config_cache = copy.deepcopy(config)
    return config


def _load_config_from_disk() -> AppConfig:
    """config.jsonをディスクから読み込む（load_config()の初回読み込み専用の内部実装）"""
    config = AppConfig()

    # JSON設定ファイルから読み込み
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, value in data.items():
                if hasattr(config, key):
                    # allowed_extensionsはデフォルトとマージ
                    if key == "allowed_extensions":
                        default_exts = set(config.allowed_extensions)
                        saved_exts = set(value)
                        merged = tuple(default_exts | saved_exts)
                        setattr(config, key, merged)
                    else:
                        setattr(config, key, value)
            logger.info(f"設定ファイル読み込み: {CONFIG_FILE}")
        except Exception as e:
            logger.warning(f"設定ファイル読み込みエラー: {e}")

    # 文字起こしエンジンは常に vibe-server.exe（UIから選べない）。見つからない場合は main.py の
    # ensure_vibe_ready() が実行中に限り Python 版へ切り替える（設定ファイルは書き換えない）
    config.whisper_backend = "vibe"

    # 表示言語（不正値は日本語に戻す）。バックエンドのメッセージもこの言語で返す
    config.ui_language = i18n.normalize_language(config.ui_language)
    i18n.set_language(config.ui_language)

    return config


def save_config(config: AppConfig) -> None:
    """設定を保存（成功時のみメモリキャッシュも更新する）"""
    global _config_cache
    try:
        data = asdict(config)
        # tupleはlistに変換
        data["allowed_extensions"] = list(data["allowed_extensions"])
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"設定ファイル保存: {CONFIG_FILE}")
        _config_cache = copy.deepcopy(config)
        i18n.set_language(config.ui_language)
    except Exception as e:
        logger.error(f"設定ファイル保存エラー: {e}")
        raise


def invalidate_config_cache() -> None:
    """config.jsonのメモリキャッシュを破棄する（次回のload_config()はディスクから再読み込みする）。
    save_config()を経由せずconfig.jsonを直接書き換えた場合（テストの`write_config`フィクスチャ等）に使用する"""
    global _config_cache
    _config_cache = None


def update_config(**kwargs) -> AppConfig:
    """設定を更新"""
    config = load_config()
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
    save_config(config)
    return config


def load_default_config() -> AppConfig:
    """デフォルト設定を読み込み（default.json）"""
    config = AppConfig()

    if DEFAULT_CONFIG_FILE.exists():
        try:
            with open(DEFAULT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, value in data.items():
                if hasattr(config, key):
                    if key == "allowed_extensions":
                        default_exts = set(config.allowed_extensions)
                        saved_exts = set(value)
                        merged = tuple(default_exts | saved_exts)
                        setattr(config, key, merged)
                    else:
                        setattr(config, key, value)
            logger.info(f"デフォルト設定読み込み: {DEFAULT_CONFIG_FILE}")
        except Exception as e:
            logger.warning(f"デフォルト設定読み込みエラー: {e}")

    return config


def save_default_config() -> None:
    """現在の設定をデフォルト設定として保存（default.json）"""
    config = AppConfig()
    try:
        data = asdict(config)
        data["allowed_extensions"] = list(data["allowed_extensions"])
        with open(DEFAULT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"デフォルト設定保存: {DEFAULT_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"デフォルト設定保存エラー: {e}")
        raise


def reset_config(tab: str = "all") -> AppConfig:
    """設定をデフォルトに戻す

    Args:
        tab: リセット対象 ("prompt", "model", "all")
    """
    current_config = load_config()
    default_config = load_default_config()
    # 初期プロンプトと音声認識の言語は、現在の表示言語の初期値にする（default.jsonは日本語の初期値のため）
    lang = current_config.ui_language
    default_config.ui_language = lang
    default_config.saved_prompts = prompt_sets.default_saved_prompts(lang)
    default_config.active_prompt_name = prompt_sets.default_active_prompt_name(lang)
    default_config.prompt = prompt_sets.default_prompt(lang)
    default_config.whisper_language = prompt_sets.default_whisper_language(lang)

    if tab == "prompt":
        # プロンプト関連のみリセット
        current_config.saved_prompts = default_config.saved_prompts
        current_config.active_prompt_name = default_config.active_prompt_name
        current_config.prompt = default_config.prompt
    elif tab == "model":
        # モデル関連のみリセット
        current_config.whisper_backend = default_config.whisper_backend
        current_config.whisper_model = default_config.whisper_model
        current_config.whisper_language = default_config.whisper_language
        current_config.vibe_server_path = default_config.vibe_server_path
        current_config.vibe_model = default_config.vibe_model
        current_config.llm_model_path = default_config.llm_model_path
        current_config.llm_device = default_config.llm_device
    else:
        # 全設定リセット（wizard_completedは保持）
        wizard_completed = current_config.wizard_completed
        current_config = default_config
        current_config.wizard_completed = wizard_completed

    save_config(current_config)
    logger.info(f"設定リセット完了 (対象: {tab})")
    return current_config
