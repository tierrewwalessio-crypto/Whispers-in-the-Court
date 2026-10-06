@echo off
REM Start Court Brain. Its window opens beside the game: keep it open while you play.
REM (run_court_brain_console.bat does the same with the old text console.)
cd /d "%~dp0"
where pyw >nul 2>nul
if errorlevel 1 (
    py -3 -m courtbrain %*
    if errorlevel 1 pause
    goto :eof
)
start "" pyw -3 -m courtbrain %*
