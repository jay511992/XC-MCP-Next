@echo off
chcp 65001 >nul
setlocal
rem Do NOT cd to this script's folder: project discovery uses caller's cwd.
where python >nul 2>nul
if errorlevel 1 (
  echo [xcbuild] python not found in PATH. Install Python 3.8+ first.
  exit /b 2
)
python "%~dp0xcbuild.py" %*
exit /b %errorlevel%
