@echo off
REM ========================================
REM Voice2Doc installer build script
REM ========================================
REM PyInstaller build, then NSIS compile.
REM Kept ASCII-only on purpose: cmd.exe reads batch files in the OEM code page and breaks on multibyte text.
cd /d "%~dp0"

echo ========================================
echo Voice2Doc installer build script
echo ========================================
echo.

REM 1. PyInstaller build
echo 1. Running the PyInstaller build...
call "%~dp0build.bat"
if errorlevel 1 (
    echo ERROR: the PyInstaller build failed
    pause
    exit /b 1
)

echo.
echo 2. Compiling the installer with NSIS...
echo    (this takes a few minutes)
echo.

REM 2. NSIS compile
"C:\Program Files (x86)\NSIS\makensis.exe" installer\voice2doc_installer.nsi
if errorlevel 1 (
    echo ERROR: the installer build failed
    echo Check that NSIS is installed correctly
    pause
    exit /b 1
)

echo.
echo ========================================
echo Installer created!
echo Output: dist\Voice2Doc-Setup.exe
echo ========================================
echo.
echo Test run:
echo    Double-click dist\Voice2Doc-Setup.exe and check that it works
echo.

pause
