@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "diagnostics.py"
) else (
  py -3 "diagnostics.py"
)
if errorlevel 1 echo 诊断未完成，请保留本窗口的错误信息。
pause
