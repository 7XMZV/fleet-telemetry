@echo off
REM Runs one sync cycle and appends the result to sync.log.
REM Double-clickable, and it is what the scheduled task invokes -- a scheduled
REM task cannot redirect output itself, so the redirection lives here.
cd /d "%~dp0"
echo.>> sync.log
echo ==== %DATE% %TIME% ====>> sync.log
python blueice_sync.py update >> sync.log 2>&1
set RC=%ERRORLEVEL%
echo ---- exit %RC%>> sync.log
exit /b %RC%
