@echo off
REM Start Court Brain with a text console as well (useful to report a problem).
cd /d "%~dp0"
py -3 -m courtbrain %*
if errorlevel 1 pause
