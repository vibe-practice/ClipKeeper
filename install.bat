@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ClipKeeper 설치

set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY py --version >nul 2>&1 && set "PY=py"
if not defined PY goto :nopython

echo [1/4] 필요한 구성요소 설치 중...
%PY% -m pip install --upgrade pillow pystray pyinstaller
if errorlevel 1 goto :fail

echo.
echo [2/4] 아이콘 만드는 중...
%PY% clipkeeper.py --make-icon
if errorlevel 1 goto :fail

echo.
echo [3/4] 실행 파일 만드는 중... 1~2분 걸려요
taskkill /im ClipKeeper.exe /f >nul 2>&1
%PY% -m PyInstaller --noconfirm --onefile --windowed --name ClipKeeper --icon clipkeeper.ico --hidden-import pystray._win32 clipkeeper.py
if errorlevel 1 goto :fail

echo.
echo [4/4] 바탕화면에 복사 중...
powershell -NoProfile -Command "Copy-Item -Force 'dist\ClipKeeper.exe' ([Environment]::GetFolderPath('Desktop'))"
if errorlevel 1 goto :fail

echo.
echo ==============================================
echo  완료! 바탕화면의 ClipKeeper를 더블클릭하세요.
echo  어디서든 Ctrl+F2 를 누르면 기록 창이 열려요.
echo ==============================================
pause
exit /b 0

:nopython
echo Python이 설치되어 있지 않아요.
echo https://www.python.org 에서 설치한 뒤 다시 실행해 주세요.
echo 설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요.
pause
exit /b 1

:fail
echo.
echo 문제가 생겼어요. 위에 나온 메시지를 복사해서 알려주세요.
pause
exit /b 1
