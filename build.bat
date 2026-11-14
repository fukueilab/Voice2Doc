@echo off
REM Voice2Doc build script (PyInstaller)
REM Kept ASCII-only on purpose: cmd.exe reads batch files in the OEM code page and breaks on multibyte text.
cd /d "%~dp0"

echo ========================================
echo Voice2Doc build script
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed
    pause
    exit /b 1
)

REM Install dependencies (requirements.txt pins the Vulkan build of llama-cpp-python with --extra-index-url)
echo 1. Installing dependencies...
pip install -r requirements.txt
pip install pyinstaller
if errorlevel 1 (
    echo ERROR: failed to install the dependencies
    pause
    exit /b 1
)

REM CPU build of the llama.cpp libraries, bundled as a fallback for PCs without a Vulkan loader
python scripts\fetch_cpu_llama_lib.py
if errorlevel 1 (
    echo ERROR: failed to fetch the CPU libraries of llama-cpp-python
    pause
    exit /b 1
)

echo.
echo 2. Building with PyInstaller...
echo    (this takes a few minutes)
echo.

pyinstaller voice2doc.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: the build failed
    pause
    exit /b 1
)

echo.
echo 3. Build complete!
echo    Output: dist\Voice2Doc\
echo.
echo 4. Test run...
echo    Start dist\Voice2Doc\Voice2Doc.exe and check that it works
echo.

pause
