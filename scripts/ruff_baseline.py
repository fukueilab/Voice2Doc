"""ruff の既存指摘（ベースライン）より指摘が増えていないかを検査する。

リポジトリには、意図的に一括修正を見送った既存コードの指摘が残っている。
そのためCIでは `ruff check .` が常に失敗してしまう。代わりに、ファイル×ルールごとの指摘件数を
`ruff-baseline.json` に記録しておき、それを**超えて増えた**場合だけ失敗させる（新規ファイルは0件から）。

使い方:
    python scripts/ruff_baseline.py            # 検査（増えていれば終了コード1）
    python scripts/ruff_baseline.py --update   # 現在の件数でベースラインを書き換える（指摘を直したとき）
"""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = ROOT / "ruff-baseline.json"


def current_counts() -> dict:
    """ruffを実行し、"ファイル::ルール" ごとの指摘件数を返す"""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json", "--exit-zero"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ruffの実行に失敗しました: {proc.stderr.strip()}")
    counts: Counter = Counter()
    for d in json.loads(proc.stdout or "[]"):
        rel = Path(d["filename"]).resolve().relative_to(ROOT).as_posix()
        counts[f"{rel}::{d.get('code') or 'syntax-error'}"] += 1
    return dict(sorted(counts.items()))


def find_regressions(current: dict, baseline: dict) -> list:
    """ベースラインより件数が増えた (キー, 基準, 現在) を返す"""
    return [(k, baseline.get(k, 0), n) for k, n in current.items() if n > baseline.get(k, 0)]


def find_improvements(current: dict, baseline: dict) -> list:
    """ベースラインより件数が減った (キー, 基準, 現在) を返す"""
    return [(k, n, current.get(k, 0)) for k, n in baseline.items() if current.get(k, 0) < n]


def main(argv: list) -> int:
    current = current_counts()
    if "--update" in argv:
        BASELINE_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"ベースラインを更新しました: {sum(current.values())}件 ({BASELINE_FILE.name})")
        return 0

    baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8")) if BASELINE_FILE.exists() else {}
    regressions = find_regressions(current, baseline)
    if regressions:
        print("ruffの指摘が増えています（ベースライン超過）:")
        for key, before, now in regressions:
            print(f"  {key}: {before} -> {now}")
        print("該当ファイルに対して `python -m ruff check <ファイル>` を実行して修正してください。")
        return 1

    improvements = find_improvements(current, baseline)
    if improvements:
        print(f"指摘が減ったルールが{len(improvements)}件あります。`--update` でベースラインを下げてください。")
    print(f"OK: 新しいruff指摘はありません（既存 {sum(current.values())}件 / ベースライン {sum(baseline.values())}件）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
