"""CPU 版 llama-cpp-python のライブラリ（lib の DLL）を build/llama_cpu_lib に取り出す。

Vulkan 版の ggml-vulkan.dll は Vulkan ローダー（vulkan-1.dll）を必須で読み込むため、ローダーの無い PC では
Vulkan 版の llama_cpp を読み込めない。ローダーそのものは再配布せず（GPU ドライバーが各 PC に入れるもの）、
代わりに CPU 版のライブラリを同梱し、ローダーが無い PC ではそちらに切り替える（local_llm.py）。
PyInstaller の前に実行する（build.bat / release.yml が呼ぶ）。

使い方: python scripts/fetch_cpu_llama_lib.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "llama_cpu_lib"
VERSION = "0.3.36"  # requirements.txt の llama-cpp-python と同じにする
CPU_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", f"llama-cpp-python=={VERSION}", "--no-deps",
             "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.12",
             "--index-url", CPU_INDEX, "-d", tmp],
            check=True,
        )
        wheels = list(Path(tmp).glob("llama_cpp_python-*.whl"))
        if not wheels:
            print("CPU 版の wheel が見つかりません", file=sys.stderr)
            return 1
        count = 0
        with zipfile.ZipFile(wheels[0]) as z:
            for name in z.namelist():
                if name.startswith("llama_cpp/lib/") and name.lower().endswith(".dll"):
                    (OUT / Path(name).name).write_bytes(z.read(name))
                    count += 1
    if (OUT / "ggml-vulkan.dll").exists():
        (OUT / "ggml-vulkan.dll").unlink()
        print("警告: CPU 版に ggml-vulkan.dll が含まれていたため削除しました", file=sys.stderr)
    print(f"CPU 版のライブラリ {count} 個を {OUT} に取り出しました")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
