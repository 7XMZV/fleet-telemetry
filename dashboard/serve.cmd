@echo off
REM Double-click to serve the dashboard on the local network.
REM Leave this window open -- closing it stops the server.
cd /d "%~dp0"
python serve.py %*
echo.
pause
