@echo off
cd /d "%~dp0"
echo Starting IRRY backend...
start "IRRY Backend" cmd /k python backend_server.py
timeout /t 3 >nul
start "" "http://127.0.0.1:5000/prototype/"
echo.
echo IRRY started.
echo PC prototype: http://127.0.0.1:5000/prototype/
echo Mobile app: http://%COMPUTERNAME%:5000/mobile/
pause
