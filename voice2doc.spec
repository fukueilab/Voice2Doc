# -*- mode: python ; coding: utf-8 -*-
"""Voice2Doc PyInstaller スペックファイル"""

import os
import sys
from pathlib import Path

block_cipher = None

# プロジェクトルート
ROOT = os.path.dirname(os.path.abspath(SPEC))

# ヒdden imports
hiddenimports = [
    # Whisper
    'whisper',
    'whisper.transcribe',
    'whisper.tokenizer',
    'whisper.decoding',
    'whisper.audio',
    'whisper.timing',
    
    # llama-cpp-python
    'llama_cpp',
    'llama_cpp.llama',
    
    
    # PyWebView
    'webview',
    'webview.window',
    'webview.platforms.winforms',
    
    # Uvicorn
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.wsproto_impl',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    
    # FastAPI
    'fastapi',
    'fastapi.middleware',
    'fastapi.middleware.cors',
    'fastapi.middleware.trustedhost',
    'fastapi.responses',
    'fastapi.responses.json',
    'fastapi.responses.html',
    'fastapi.staticfiles',
    
    # Starlette
    'starlette',
    'starlette.responses',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.staticfiles',
    
    # Pydantic
    'pydantic',
    'pydantic.fields',
    'pydantic.main',
    'pydantic.json_schema',
    
    # HTTP
    'httpx',
    'httpx._transports',
    'httpx._transports.default',
    
    # multipart
    'multipart',
    'multipart.multipart',
    
    # Windows specific
    'ctypes',
    'ctypes.wintypes',
    'ctypes.windll',
    'ctypes.windll.kernel32',

    # Standard library
    'json',
    'logging',
    'pathlib',
    'typing',
    'dataclasses',
    'asyncio',
    'threading',
    'subprocess',
    'shutil',
    'tempfile',
    'time',
    'os',
    'sys',
    're',
    'io',
    'base64',
    # 出力形式変換 (Word/PDF)
    'docx',
    'fpdf',
    'PIL',
    'defusedxml',
    'fontTools',
]

# データファイル
datas = [
    (os.path.join(ROOT, 'src'), 'src'),
    # PDF出力用の日本語フォント(IPAexゴシック)とそのライセンス文書
    (os.path.join(ROOT, 'fonts'), 'fonts'),
    # アプリアイコン（root main.pyのwebview.start(icon=...)からも参照する）
    (os.path.join(ROOT, 'assets', 'icon.ico'), 'assets'),
]

# python-docx の既定テンプレート(default.docx 等)をWord出力のために同梱
from PyInstaller.utils.hooks import collect_data_files
datas += collect_data_files('docx')
# openai-whisper（予備の文字起こし）が実行時に読むデータ（メルフィルター・語彙）を同梱
datas += collect_data_files('whisper')

# バイナリファイル
import importlib.util
_llama_cpp_spec = importlib.util.find_spec('llama_cpp')
_llama_cpp_path = os.path.dirname(_llama_cpp_spec.origin) if _llama_cpp_spec else None
_llama_cpp_lib = os.path.join(_llama_cpp_path, 'lib') if _llama_cpp_path else None

# vibe-server.exe・ffmpeg.exe は同梱しない（利用者がインストールした Vibe のものを使う）
binaries = []

# llama-cpp-python の DLL をバンドル
if _llama_cpp_lib and os.path.exists(_llama_cpp_lib):
    for f in os.listdir(_llama_cpp_lib):
        if f.endswith(('.dll', '.pyd')):
            binaries.append((os.path.join(_llama_cpp_lib, f), 'llama_cpp/lib'))
    # Vulkan 版の ggml-vulkan.dll は Vulkan ローダー（vulkan-1.dll）を必須で読み込む。ローダーは再配布せず、
    # ローダーの無い PC（GPU ドライバーが無い仮想マシン等）では、同梱した CPU 版のライブラリに切り替える
    # （local_llm.prepare_llama_cpp_library）。CPU 版は scripts/fetch_cpu_llama_lib.py が build/llama_cpu_lib に用意する
    if os.path.exists(os.path.join(_llama_cpp_lib, 'ggml-vulkan.dll')):
        _cpu_lib = os.path.join(ROOT, 'build', 'llama_cpu_lib')
        if not os.path.isdir(_cpu_lib) or not os.listdir(_cpu_lib):
            raise SystemExit('build/llama_cpu_lib is missing: run "python scripts/fetch_cpu_llama_lib.py" before PyInstaller')
        for f in os.listdir(_cpu_lib):
            if f.endswith('.dll'):
                # binaries ではなく datas にする（binaries だと依存解析で CPU 版の llama.dll 等が
                # アプリ直下にも置かれ、lib の Vulkan 版を隠してしまう）
                datas.append((os.path.join(_cpu_lib, f), 'llama_cpp/lib_cpu'))

a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    pathex=[ROOT, os.path.join(ROOT, 'src_backends')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'pandas',
        'scipy',
        # 'PIL' は除外しない: fpdf2(PDF出力)がPillowをimportするため
        'cv2',
        # 開発・テスト専用。torch・numba の任意の import をたどって同梱されるのを防ぐ（実行時には使わない）
        'pytest',
        '_pytest',
        'coverage',
        'pygments',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# lib_cpu の DLL を依存解決の探索先として拾い、CPU 版の llama.dll 等がアプリ直下にも置かれて
# llama_cpp/lib の Vulkan 版を隠すのを防ぐ（lib_cpu 自体は datas として残す）
_cpu_lib_dir = os.path.join(ROOT, 'build', 'llama_cpu_lib').lower()
a.binaries = [b for b in a.binaries
              if not (os.path.abspath(b[1]).lower().startswith(_cpu_lib_dir) and not b[0].lower().startswith('llama_cpp'))]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Voice2Doc',
    debug=False,  # デバッグモード無効（リリース用）
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # コンソール非表示（リリース用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, 'assets', 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Voice2Doc',
)
