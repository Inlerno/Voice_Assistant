@echo off
setlocal
title Astra
cd /d "%~dp0"

rem ===== 1. Find or download the project =====
if exist "pyproject.toml" goto have_project
if exist "Astra\pyproject.toml" goto enter_folder

echo [1/3] Downloading Astra, please wait...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Remove-Item -Recurse -Force 'Astra','%TEMP%\astra_unpack' -ErrorAction SilentlyContinue; Invoke-WebRequest 'https://github.com/Inlerno/Voice_Assistant/archive/refs/heads/main.zip' -OutFile '%TEMP%\astra.zip'; Expand-Archive -Force '%TEMP%\astra.zip' '%TEMP%\astra_unpack'; Move-Item -Force '%TEMP%\astra_unpack\Voice_Assistant-main' 'Astra'"
if errorlevel 1 goto fail_download

:enter_folder
cd Astra

:have_project
rem Update the project when it was cloned with git
if exist ".git" (
    where git >nul 2>nul && git pull --ff-only
)

rem ===== 2. Find or install uv (it also installs the right Python) =====
where uv >nul 2>nul
if not errorlevel 1 goto have_uv
echo [2/3] Installing uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 goto fail_uv

:have_uv
rem ===== 3. Install the packages and start =====
echo [3/3] Preparing packages (the first time takes a few minutes)...
uv sync
if errorlevel 1 goto fail_sync
echo Starting Astra...
uv run python run.py
if errorlevel 1 goto fail_run
exit /b 0

:fail_download
echo.
echo Could not download the project. Check the internet connection and run the file again.
goto end
:fail_uv
echo.
echo Could not install uv. Install it by hand from https://docs.astral.sh/uv/ and run the file again.
goto end
:fail_sync
echo.
echo Could not install the packages. Send a screenshot of this window to the developer.
goto end
:fail_run
echo.
echo Astra stopped with an error. Send a screenshot of this window to the developer.
:end
pause
exit /b 1
