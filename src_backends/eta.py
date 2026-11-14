"""処理中に表示する時間情報の計算

- **音声認識**は音声長に比例して所要時間が決まるため、「推定: ○分○秒」を出す。
  係数（所要時間 / 音声長）は実測から学習して保存する。表示のたびに現在時刻から再計算するので、
  進捗コールバックが呼ばれない長いステップ中も残り時間が減っていく。
- **AI生成（LLM）**は出力の長さが事前に分からず所要時間を予測できないため、推定は出さず**経過時間**を出す。
"""
import json
import logging
from pathlib import Path
from typing import Optional

from . import config as _config
from .i18n import t

logger = logging.getLogger("voice2doc.eta")

# 音声長に対する所要時間の比（学習前の初期値）
DEFAULT_FACTORS = {"vibe": 0.15, "python": 0.4}
MIN_FACTOR = 0.01
MAX_FACTOR = 3.0
# 学習時、この秒数未満の音声は誤差が大きいので無視する
MIN_LEARN_AUDIO_SEC = 5.0
# 学習の指数移動平均の重み（新しい実測の比率）
EMA_ALPHA = 0.5

# 音声長に比例しない固定の所要時間（動画の音声抽出、Pythonバックエンドのモデル読み込み）
OVERHEAD_FFMPEG_SEC = 5.0
OVERHEAD_MODEL_SEC = 3.0
MIN_PREDICTION_SEC = 3.0

RECOGNITION_STEPS = ("ffmpeg", "model", "transcribe")


def _stats_path() -> Path:
    # テストでCACHE_DIRを差し替えられるよう、呼び出し時に参照する
    return _config.CACHE_DIR / "eta_stats.json"


def _backend_key(backend: str) -> str:
    return backend if backend in DEFAULT_FACTORS else "vibe"


def _load_stats() -> dict:
    try:
        data = json.loads(_stats_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_factor(backend: str) -> float:
    """学習済みの係数（所要時間/音声長）。無い・壊れている・範囲外なら既定値"""
    key = _backend_key(backend)
    value = _load_stats().get(key)
    if isinstance(value, (int, float)) and MIN_FACTOR <= value <= MAX_FACTOR:
        return float(value)
    return DEFAULT_FACTORS[key]


def _overhead(is_video: bool, backend: str) -> float:
    return (OVERHEAD_FFMPEG_SEC if is_video else 0.0) + (OVERHEAD_MODEL_SEC if backend != "vibe" else 0.0)


def predict_recognition_seconds(audio_duration: float, factor: float, is_video: bool, backend: str) -> float:
    """音声認識（音声抽出・モデル読み込み・文字起こし）全体の予測秒数"""
    return max(audio_duration * factor + _overhead(is_video, backend), MIN_PREDICTION_SEC)


def record_run(backend: str, elapsed_sec: float, audio_duration: float, is_video: bool,
               transcribe_only: bool = False) -> None:
    """音声認識1回分の実測から係数を学習して保存する。失敗しても処理には影響させない。

    ``transcribe_only``が真なら``elapsed_sec``は文字起こし本体だけの時間（固定オーバーヘッドを引かない）。"""
    if audio_duration < MIN_LEARN_AUDIO_SEC or elapsed_sec <= 0:
        return
    key = _backend_key(backend)
    overhead = 0.0 if transcribe_only else _overhead(is_video, backend)
    observed = max(elapsed_sec - overhead, 0.0) / audio_duration
    observed = min(max(observed, MIN_FACTOR), MAX_FACTOR)
    previous = load_factor(backend)
    updated = EMA_ALPHA * observed + (1 - EMA_ALPHA) * previous
    try:
        stats = _load_stats()
        stats[key] = round(updated, 4)
        path = _stats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning(f"推定時間の学習結果を保存できませんでした: {e}")


def format_elapsed(seconds: float) -> str:
    """経過時間 m:ss（1時間以上は h:mm:ss）"""
    total = max(int(seconds), 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_remaining(seconds: float) -> str:
    """残り時間の表示（○秒 / ○分○秒 / ○時間○分）"""
    total = max(int(seconds), 0)
    if total >= 3600:
        return t("eta.hm", h=total // 3600, m=(total % 3600) // 60)
    if total >= 60:
        return t("eta.ms", m=total // 60, s=total % 60)
    return t("eta.s", s=total)


def compute_eta_text(status: dict, now: float) -> Optional[str]:
    """処理状態から、画面の進捗詳細に出す時間表示（推定 or 経過）を返す。出せなければ None"""
    step = status.get("current_step")

    if step == "llm":
        started = status.get("step_started_at")
        if not started:
            return None
        return t("eta.ai_elapsed", t=format_elapsed(now - started))

    if step in RECOGNITION_STEPS:
        started = status.get("recognition_started_at")
        predicted = status.get("recognition_predicted_sec")
        if not started or not predicted:
            return None
        elapsed = now - started
        remaining = predicted - elapsed
        if remaining > 0:
            return t("eta.estimate", t=format_remaining(remaining))
        return t("eta.over", t=format_elapsed(elapsed))

    return None
