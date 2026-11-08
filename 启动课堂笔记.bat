@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto create_venv
goto check_dependencies

:create_venv
echo Preparing the app's private Python environment...
py -3 -m venv "%~dp0.venv"
if errorlevel 1 goto no_python

:check_dependencies
"%~dp0.venv\Scripts\python.exe" -c "import numpy, PIL, mss, soundcard, faster_whisper, av, requests, keyring, jwt, docx, reportlab, video_import" >nul 2>nul
if errorlevel 1 goto install_dependencies
goto launch_app

:install_dependencies
echo Installing required Python packages. This may take several minutes and needs internet access.
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto install_failed

:launch_app
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0lesson_notes_automation.py"
exit /b 0

:no_python
echo Python 3.10+ with Tkinter was not found.
echo Install Python from https://www.python.org/downloads/windows/ and enable Tcl/Tk support.
pause
exit /b 1

:install_failed
echo.
echo Dependency installation failed. Check your internet connection, then run this launcher again.
pause
exit /b 1
