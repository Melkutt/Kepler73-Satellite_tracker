@echo off
REM Build Kepler73.exe with PyInstaller  ->  dist\Kepler73.exe
setlocal
cd /d "%~dp0"

echo == Installing build dependencies ==
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :err

echo.
echo == Building (this takes a minute) ==
python -m PyInstaller --clean --noconfirm kepler73.spec
if errorlevel 1 goto :err

echo.
echo == Done ==
echo   dist\Kepler73.exe
echo.
echo Test it, then attach dist\Kepler73.exe to a GitHub Release.
pause
exit /b 0

:err
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
