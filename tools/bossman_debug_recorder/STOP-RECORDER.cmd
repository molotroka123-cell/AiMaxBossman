@echo off
cd /d "%~dp0"
type nul > stop.request
echo Stop requested. The recorder will save SUMMARY.md in a few seconds.
timeout /t 3 /nobreak >nul
if exist LATEST.txt (
  set /p LAST=<LATEST.txt
  explorer "%LAST%"
)
