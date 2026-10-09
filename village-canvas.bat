@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\python\python.exe" (
  "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0village_canvas_cli_hidden.ps1" %*
) else (
  echo Portable runtime\python\python.exe was not found.
  exit /b 1
)
endlocal
