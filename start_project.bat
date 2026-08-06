@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv\Scripts\python.exe
  echo Install the 0.2.0 wheel by following 启动说明.md first.
  pause
  exit /b 1
)

set "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=%~dp0log"

echo Starting JupyterLab with authentication enabled.
echo Copy the complete token URL printed below into your browser.
echo Press Ctrl+C in this window to stop the server.
echo.

".venv\Scripts\python.exe" -m jupyter lab --no-browser
set "JUPYTER_EXIT_CODE=%ERRORLEVEL%"

if not "%JUPYTER_EXIT_CODE%"=="0" (
  echo.
  echo JupyterLab exited with code %JUPYTER_EXIT_CODE%.
  pause
)

exit /b %JUPYTER_EXIT_CODE%
