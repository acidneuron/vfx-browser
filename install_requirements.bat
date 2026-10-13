@echo off
setlocal
cd /d %~dp0

echo === VFX Asset Browser - setup ===
echo.

REM --- check uv ---
where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv not found on PATH. Install it with:
  echo     winget install astral-sh.uv
  echo then reopen the terminal and run this script again.
  exit /b 1
)

REM --- check ffmpeg ---
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [ERROR] ffmpeg not found on PATH. Install it with:
  echo     winget install Gyan.FFmpeg
  echo then reopen the terminal and run this script again.
  exit /b 1
)

echo [1/2] Creating virtual environment (.venv)...
if exist .venv\Scripts\python.exe (
  echo .venv already exists - skipping creation.
) else (
  uv venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create the virtual environment.
    exit /b 1
  )
)

echo [2/2] Installing requirements...
uv pip install --python .venv -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install the requirements.
  exit /b 1
)

echo.
echo === Setup complete ===
echo Start the app with:  run.bat
echo Then open:           http://127.0.0.1:8765
endlocal
