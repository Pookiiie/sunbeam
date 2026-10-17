@echo off
setlocal
title GameShelf
cd /d "%~dp0"

rem ---- the Windows download: its own Python, no console window ---------------
if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" -m gameshelf --app %*
  exit /b 0
)

rem ---- a copy without the runtime folder: use Python 3.10+ from this PC --------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "PY=python"
)

if not defined PY (
  echo.
  echo   This copy of GameShelf needs Python 3.10 or newer.
  echo   The easiest fix: download GameShelf from https://github.com/Pookiiie/GameShelf/releases
  echo   (that download includes everything it needs^).
  echo.
  start "" "https://github.com/Pookiiie/GameShelf/releases"
  pause
  exit /b 1
)

rem ---- optional extras: HowLongToBeat and the tray icon (installed once) ------
%PY% -c "import howlongtobeatpy, pystray, PIL" >nul 2>&1
if errorlevel 1 (
  echo   Setting up, one moment...
  %PY% -m pip install --user --quiet --disable-pip-version-check howlongtobeatpy pystray pillow >nul 2>&1
)

%PY% -m gameshelf --app %*
if errorlevel 1 pause
exit /b
