"""ローカルLLMモジュール (llama-cpp-python)"""
import atexit
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from .config import DEFAULT_LLM_DEVICE, LLM_DEVICES, normalize_llm_device  # noqa: F401
from .i18n import get_language, t
from .path_utils import app_data_dir, get_cache_base_dir, is_frozen
from .prompt_sets import default_prompt

logger = logging.getLogger("voice2doc.local_llm")

# huggingface_hub が、モデルの取得とは別に AI ツールの一覧を取得したり、利用環境の情報（PyTorch の版など）を
# 送ったりしないようにする（import のときに読まれるため、最初に設定する）
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

# llama-cpp-python 設定
LLAMACPP_MODEL_REPO = "unsloth/gemma-4-E2B-it-qat-GGUF"
LLAMACPP_MODEL_FILENAME = "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
LLAMACPP_MODEL_SIZE_GB = 2.62

# モデルキャッシュ先 (PyInstaller対応)
if is_frozen():
    CACHE_DIR = get_cache_base_dir() / ".cache" / "llm"
else:
    CACHE_DIR = app_data_dir(Path(os.path.expanduser("~")) / "Documents") / ".cache" / "llm"
LLAMACPP_MODEL_PATH = CACHE_DIR / LLAMACPP_MODEL_FILENAME

_llama_cpp_available: Optional[bool] = None
_lib_prepared = False


def _vulkan_loader_available() -> bool:
    """この PC に Vulkan ローダー（vulkan-1.dll。GPU ドライバーが入れる）があるか"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.WinDLL("vulkan-1.dll")
        return True
    except OSError:
        return False


def prepare_llama_cpp_library() -> None:
    """llama_cpp を読み込む前に、使うライブラリを選ぶ（インストール版のみ）。

    Vulkan 版の ggml-vulkan.dll は vulkan-1.dll を必須で読み込むため、ローダーの無い PC（GPU ドライバーが
    無い仮想マシン等）では Vulkan 版の llama_cpp を読み込めない。ローダー自体は再配布せず、同梱した CPU 版の
    ライブラリ（llama_cpp/lib_cpu）に切り替える。利用者が LLAMA_CPP_LIB_PATH を指定している場合は触らない。"""
    global _lib_prepared
    if _lib_prepared:
        return
    _lib_prepared = True
    if not is_frozen() or "llama_cpp" in sys.modules or os.environ.get("LLAMA_CPP_LIB_PATH"):
        return
    cpu_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "llama_cpp" / "lib_cpu"
    if not cpu_dir.exists() or _vulkan_loader_available():
        return
    os.environ["LLAMA_CPP_LIB_PATH"] = str(cpu_dir)
    logger.warning(f"Vulkan ローダーが見つからないため、CPU 版のライブラリを使います: {cpu_dir}")


def is_llama_cpp_available() -> bool:
    """llama-cpp-python が利用可能かチェック"""
    global _llama_cpp_available
    prepare_llama_cpp_library()
    if _llama_cpp_available is not None:
        return _llama_cpp_available

    try:
        from llama_cpp import Llama
        _llama_cpp_available = True
        logger.info("llama-cpp-python 利用可能")
    except ImportError as e:
        _llama_cpp_available = False
        logger.warning(f"llama-cpp-python が利用できません: {e}")
    except Exception as e:
        _llama_cpp_available = False
        logger.warning(f"llama-cpp-python チェックエラー: {e}")

    return _llama_cpp_available


def ensure_llamacpp_model_downloaded(progress_callback=None) -> Path:
    """llama-cpp-python用モデルをダウンロード"""
    if LLAMACPP_MODEL_PATH.exists():
        logger.info(f"llama-cpp-python モデル済み: {LLAMACPP_MODEL_PATH}")
        if progress_callback:
            progress_callback("LLMモデル準備完了", 100)
        return LLAMACPP_MODEL_PATH

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"モデルダウンロード開始: {LLAMACPP_MODEL_REPO}/{LLAMACPP_MODEL_FILENAME}")

    if progress_callback:
        progress_callback(t("llm.model_dl"), 0)

    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(
            repo_id=LLAMACPP_MODEL_REPO,
            filename=LLAMACPP_MODEL_FILENAME,
            cache_dir=str(CACHE_DIR),
            local_dir=str(CACHE_DIR),
        )

        downloaded_path = Path(path)
        if downloaded_path != LLAMACPP_MODEL_PATH:
            import shutil
            shutil.move(str(downloaded_path), str(LLAMACPP_MODEL_PATH))

        logger.info(f"モデルダウンロード完了: {LLAMACPP_MODEL_PATH}")
        if progress_callback:
            progress_callback("LLMモデル準備完了", 100)
        return LLAMACPP_MODEL_PATH

    except Exception as e:
        logger.error(f"モデルダウンロードエラー: {e}")
        if progress_callback:
            progress_callback("LLMモデルダウンロードエラー", 0)
        raise


def ensure_model_downloaded(progress_callback=None):
    """llama-cpp-python用モデルの存在確認・ダウンロード"""
    return ensure_llamacpp_model_downloaded(progress_callback)


# ==================== llama-cpp-python クライアント ====================

_LLM_LOCK = threading.RLock()

# この実行中のプロセスが GPU 読み込みの印を作ったか。自分が作った印は「前回の強制終了」とは見なさない
# （GPU で読み込んだあと、最初の生成までに設定を切り替えた場合など）
_marker_owned = False


def gpu_probe_path() -> Path:
    """GPU 読み込み中の印のファイル（%LOCALAPPDATA%\\Voice2Doc\\llm_gpu_probe）。

    GPU で読み込む直前に作り、読み込みと最初の生成が成功したら消す。残っていれば、前回の起動で
    ドライバー側の不具合により強制終了した可能性があるため、次回から自動で CPU にする。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Voice2Doc" / "llm_gpu_probe"


def _create_probe_marker() -> None:
    global _marker_owned
    try:
        p = gpu_probe_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("loading", encoding="utf-8")
        _marker_owned = True
    except Exception as e:
        logger.warning(f"GPU 読み込みの印を作れませんでした: {e}")


def _remove_probe_marker() -> None:
    global _marker_owned
    try:
        gpu_probe_path().unlink(missing_ok=True)
        _marker_owned = False
    except Exception as e:
        logger.warning(f"GPU 読み込みの印を消せませんでした: {e}")


def gpu_crash_marker_exists() -> bool:
    """前回の GPU 読み込みが完了しないまま終了したことを示す印があるか（この実行中に作った印は除く）"""
    try:
        return gpu_probe_path().exists() and not _marker_owned
    except Exception:
        return False


_gpu_supported: Optional[bool] = None


def gpu_offload_supported() -> bool:
    """この llama-cpp-python が GPU オフロード（Vulkan 版）に対応し、使える装置があるか（結果は失敗も含めて保持）"""
    global _gpu_supported
    if _gpu_supported is None:
        try:
            prepare_llama_cpp_library()
            import llama_cpp
            _gpu_supported = bool(llama_cpp.llama_supports_gpu_offload())
        except Exception as e:
            logger.warning(f"GPU 対応の確認に失敗しました: {e}")
            _gpu_supported = False
    return _gpu_supported


def gpu_device_name() -> str:
    """利用できる GPU（iGPU を含む）の名前を取得する。取得できなければ空文字"""
    try:
        import ctypes

        import llama_cpp
        libdir = Path(llama_cpp.__file__).parent / "lib"
        ggml = ctypes.CDLL(str(libdir / "ggml.dll"))
        base = ctypes.CDLL(str(libdir / "ggml-base.dll"))

        def fn(name, restype, argtypes):
            for lib in (ggml, base):
                if hasattr(lib, name):
                    f = getattr(lib, name)
                    f.restype, f.argtypes = restype, argtypes
                    return f
            raise AttributeError(name)

        count = fn("ggml_backend_dev_count", ctypes.c_size_t, [])
        get = fn("ggml_backend_dev_get", ctypes.c_void_p, [ctypes.c_size_t])
        dtype = fn("ggml_backend_dev_type", ctypes.c_int, [ctypes.c_void_p])
        desc = fn("ggml_backend_dev_description", ctypes.c_char_p, [ctypes.c_void_p])
        for i in range(count()):
            dev = get(i)
            if dtype(dev) in (1, 2):  # GGML_BACKEND_DEVICE_TYPE_GPU / IGPU
                return (desc(dev) or b"").decode("utf-8", "replace").strip()
    except Exception as e:
        logger.debug(f"GPU 名を取得できませんでした: {e}")
    return ""


# 実際に使っている装置の状態（/llm/status で返す）
_device_state: dict = {"device": None, "device_name": "", "reason": ""}


def get_llm_device_status(configured: str = DEFAULT_LLM_DEVICE) -> dict:
    """LLM の実行装置の状態を返す。device は読み込み後に "gpu" / "cpu"、未読み込みなら None。

    reason: ""（通常）/ "cpu_selected"（設定で CPU）/ "no_gpu"（GPU 非対応・装置なし）/
    "previous_crash"（前回 GPU で強制終了したため CPU）/ "gpu_load_failed"（GPU で読み込めず CPU）"""
    return {
        "configured": normalize_llm_device(configured),
        "device": _device_state["device"],
        "device_name": _device_state["device_name"],
        "reason": _device_state["reason"],
        "gpu_available": gpu_offload_supported(),
        "gpu_disabled_by_crash": gpu_crash_marker_exists(),
    }


def _set_device_state(device: Optional[str], name: str = "", reason: str = "") -> None:
    _device_state.update({"device": device, "device_name": name, "reason": reason})


class LlamaCppClient:
    """llama-cpp-python LLMクライアント"""

    def __init__(self, model_path: Optional[str] = None, device: str = DEFAULT_LLM_DEVICE):
        self.model_path = Path(model_path) if model_path else LLAMACPP_MODEL_PATH
        self.device = normalize_llm_device(device)
        self.device_used: Optional[str] = None  # "gpu" / "cpu"（読み込み後）
        self._model = None
        self._probe_pending = False  # GPU で読み込み済みだが、最初の生成がまだ成功していない

    def _create_llama(self, n_gpu_layers: int):
        from llama_cpp import Llama

        return Llama(
            model_path=str(self.model_path),
            n_ctx=8192,
            n_threads=4,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )

    def _decide_device(self) -> tuple[bool, str]:
        """GPU で読み込むか (use_gpu, 理由) を決める"""
        if self.device == "cpu":
            return False, "cpu_selected"
        if not gpu_offload_supported():
            return False, "no_gpu"
        # 自動のときだけ、前回の強制終了を理由に CPU へ切り替える（GPU を明示した場合は試す。
        # 成功すれば印が消え、自動でも GPU に戻る）
        if self.device == "auto" and gpu_crash_marker_exists():
            return False, "previous_crash"
        return True, ""

    def _load_model(self):
        if self._model is not None:
            return

        if not is_llama_cpp_available():
            raise ImportError(t("llm.unavailable_err", error="llama_cpp"))

        if not self.model_path.exists():
            raise FileNotFoundError(t("llm.model_file_missing", path=self.model_path))

        logger.info(f"llama-cpp-python モデル読み込み中: {self.model_path}（実行装置の設定: {self.device}）")

        use_gpu, reason = self._decide_device()
        if use_gpu:
            name = gpu_device_name()
            _create_probe_marker()
            try:
                self._model = self._create_llama(-1)
                self.device_used = "gpu"
                self._probe_pending = True
                _set_device_state("gpu", name, "")
                logger.info(f"llama-cpp-python モデル読み込み完了（GPU: {name or '名称不明'}）")
                return
            except Exception as e:
                logger.warning(f"GPU でのモデル読み込みに失敗したため CPU で読み込み直します: {e}")
                _remove_probe_marker()
                reason = "gpu_load_failed"
        elif reason == "previous_crash":
            logger.warning("前回 GPU でのモデル読み込み中に強制終了した可能性があるため、CPU で読み込みます"
                           "（設定で実行装置を GPU にして成功すると戻ります）")

        if _marker_owned:
            # 直前に GPU で読み込んだ印が残っている（生成前に設定が切り替わった）。読み込み自体は成功しているので消す
            _remove_probe_marker()
        self._model = self._create_llama(0)
        self.device_used = "cpu"
        self._probe_pending = False
        _set_device_state("cpu", "", reason)
        logger.info(f"llama-cpp-python モデル読み込み完了（CPU。理由: {reason or '既定'}）")

    def _gpu_generation_ok(self) -> None:
        """GPU で最初の生成が成功したら、強制終了検知用の印を消す"""
        if self._probe_pending:
            self._probe_pending = False
            _remove_probe_marker()

    def _fallback_to_cpu(self, error: Exception) -> bool:
        """GPU での生成に失敗したとき、モデルを CPU で読み込み直す。切り替えたら True"""
        if self.device_used != "gpu":
            return False
        if isinstance(error, ValueError):
            # 入力が長すぎる等の入力側のエラー。CPU で読み直しても同じなので切り替えない
            return False
        logger.warning(f"GPU での生成に失敗したため CPU に切り替えます: {error}")
        # 例外のトレースバックが GPU のモデルを参照し続けないよう外し、先に解放してから CPU で読み込む
        error.__traceback__ = None
        self._model = None
        import gc
        gc.collect()
        _remove_probe_marker()
        self._probe_pending = False
        try:
            self._model = self._create_llama(0)
        except Exception as e:
            logger.error(f"CPU での読み込み直しにも失敗しました: {e}")
            return False
        self.device_used = "cpu"
        _set_device_state("cpu", "", "gpu_load_failed")
        return True

    def generate_report(
        self,
        transcript: str,
        filename: str,
        prompt: Optional[str] = None,
    ) -> str:
        self._load_model()

        system_instruction = prompt or default_prompt(get_language())

        user_message = t("llm.user_message", transcript=transcript)

        logger.info(f"llama-cpp-python LLM生成開始: {filename}")

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_message},
        ]

        try:
            response = self._model.create_chat_completion(
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            )
        except Exception as e:
            if not self._fallback_to_cpu(e):
                raise
            response = self._model.create_chat_completion(
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            )
        self._gpu_generation_ok()

        result = response["choices"][0]["message"]["content"]
        logger.info(f"llama-cpp-python LLM生成完了: {len(result)}文字")
        return result

    def generate_report_stream(
        self,
        transcript: str,
        filename: str,
        on_chunk=None,
        is_cancelled=None,
        prompt: Optional[str] = None,
    ) -> str:
        # Llamaはスレッドセーフではない。中止後に取り残された生成スレッドと次の処理が
        # 同じモデルを同時に使わないよう直列化する（取り残された側は待機後すぐ中止で抜ける）
        with _LLM_LOCK:
            if is_cancelled and is_cancelled():
                return ""
            return self._generate_report_stream_locked(transcript, filename, on_chunk, is_cancelled, prompt)

    def _generate_report_stream_locked(self, transcript, filename, on_chunk, is_cancelled, prompt) -> str:
        self._load_model()

        system_instruction = prompt or default_prompt(get_language())

        user_message = t("llm.user_message_named", filename=filename, transcript=transcript)

        logger.info(f"llama-cpp-python LLMストリーム生成開始: {filename}")

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_message},
        ]

        full_text = ""

        def run_stream():
            nonlocal full_text
            stream = self._model.create_chat_completion(
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
                stream=True,
            )
            for chunk in stream:
                if is_cancelled and is_cancelled():
                    logger.info("llama-cpp-python ストリーム: 中止リクエストを検出")
                    break
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    full_text += content
                    # 1 トークン出た時点で GPU の読み込み・計算は動いているので、強制終了検知の印を消す
                    self._gpu_generation_ok()
                    if on_chunk:
                        on_chunk(full_text)

        try:
            try:
                run_stream()
            except Exception as e:
                # まだ何も出力していないときだけ、GPU の失敗を CPU で再実行する
                if full_text or not self._fallback_to_cpu(e):
                    raise
                run_stream()
            if full_text:
                self._gpu_generation_ok()
        except Exception as e:
            logger.error(f"llama-cpp-python ストリームエラー: {e}")
            raise

        logger.info(f"llama-cpp-python LLMストリーム生成完了: {len(full_text)}文字")
        return full_text


# ==================== 統合インターフェース ====================

_llamacpp_client: Optional[LlamaCppClient] = None


def get_llamacpp_client(model_path: Optional[str] = None, device: str = DEFAULT_LLM_DEVICE) -> LlamaCppClient:
    """LlamaCppClientを取得

    ``model_path`` または ``device`` が前回と異なる場合（設定の「AIモデルの指定」「LLM の実行装置」を
    変更した場合）はクライアントを作り直す（旧クライアントが保持するモデルは参照が外れ解放される）。
    """
    global _llamacpp_client
    wanted = Path(model_path) if model_path else LLAMACPP_MODEL_PATH
    device = normalize_llm_device(device)
    with _LLM_LOCK:  # 生成・読み込み直しと同時にクライアントを差し替えない
        if _llamacpp_client is None or _llamacpp_client.model_path != wanted or _llamacpp_client.device != device:
            if _llamacpp_client is not None:
                logger.info(f"AIモデル/実行装置を切り替えます: {_llamacpp_client.model_path}({_llamacpp_client.device})"
                            f" -> {wanted}({device})")
                _set_device_state(None)
            _llamacpp_client = LlamaCppClient(model_path, device)
        return _llamacpp_client


def _cleanup_probe_marker_at_exit() -> None:
    """正常終了時は、この実行中に作った GPU 読み込みの印を消す（強制終了と誤認させない）"""
    if _marker_owned:
        _remove_probe_marker()


atexit.register(_cleanup_probe_marker_at_exit)



def preload_model(model_path: Optional[str] = None, device: str = DEFAULT_LLM_DEVICE) -> None:
    """モデルを先に読み込む（設定変更後に「実際に使っている装置」を表示するため）"""
    if not is_llama_cpp_available():
        return
    with _LLM_LOCK:
        client = get_llamacpp_client(model_path, device)
        if client.model_path.exists():
            client._load_model()
            if client._probe_pending:
                # GPU で読み込めたら、1 トークンだけ生成して動作を確かめ、強制終了検知用の印を消す
                try:
                    client._model.create_chat_completion(
                        messages=[{"role": "user", "content": "ok"}], temperature=0.0, max_tokens=1)
                    client._gpu_generation_ok()
                except Exception as e:
                    client._fallback_to_cpu(e)


def generate_report(
    transcript: str,
    filename: str,
    prompt: Optional[str] = None,
    model_path: Optional[str] = None,
    device: str = DEFAULT_LLM_DEVICE,
) -> str:
    """
    LLMでレポート生成（llama-cpp-python固定）

    Args:
        transcript: 音声テキスト
        filename: ファイル名
        prompt: プロンプト
        model_path: llama-cpp-python用モデルパス

    Returns:
        str: 生成されたテキスト
    """
    if not is_llama_cpp_available():
        raise RuntimeError(t("llm.unavailable"))

    client = get_llamacpp_client(model_path, device)
    try:
        return client.generate_report(
            transcript=transcript,
            filename=filename,
            prompt=prompt,
        )
    except Exception as e:
        logger.error(f"llama-cpp-python LLMエラー: {e}")
        raise

