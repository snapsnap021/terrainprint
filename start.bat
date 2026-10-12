@echo off
setlocal
title Terrain + Route STL Generator

rem Run from this file's folder (pushd also handles \\server\share paths)
pushd "%~dp0"

rem Keep the Python environment on the local disk: rasterio's DLLs fail to
rem load from long network-share paths ("filename or extension is too long").
set "VENV=%LOCALAPPDATA%\terrainprint\venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo  First run: setting things up. This takes a few minutes, please wait...
    echo.
    python -m venv "%VENV%" 2>nul || py -m venv "%VENV%" 2>nul
    if not exist "%PY%" goto :nopython
)

echo  Checking required packages...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo  ============================================================
echo   The app is running. Your browser will open in a moment.
echo   If it doesn't, go to:  http://127.0.0.1:8000
echo.
echo   KEEP THIS WINDOW OPEN while you use the app.
echo   Close this window when you're finished.
echo  ============================================================
echo.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
"%PY%" -m uvicorn app:app
echo.
echo  The app has stopped.
pause
goto :eof

:nopython
echo.
echo  Could not find Python. Install it from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH" during installation, then try again.
echo.
pause
goto :eof

:failed
echo.
echo  Installing the required packages failed. Take a screenshot of this
echo  window and send it to whoever is helping you.
echo.
pause
