@echo off
chcp 65001 >nul 2>&1
title DowFlow Debug Center
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERREUR] Python introuvable dans le PATH.
    echo Installe Python et coche "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

if exist "%~dp0.venv\Scripts\activate.bat" (
    call "%~dp0.venv\Scripts\activate.bat"
)

REM FFmpeg / aria2 au PATH (meme logique que start.bat)
set "FFMPEG_BIN=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin"
if exist "%FFMPEG_BIN%\ffmpeg.exe" set "PATH=%FFMPEG_BIN%;%PATH%"
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\aria2.aria2*") do (
    for /d %%S in ("%%D\aria2*") do (
        if exist "%%S\aria2c.exe" set "PATH=%%S;%PATH%"
    )
)

python "%~dp0debug_center.py"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
    echo.
    echo [ERREUR] debug_center.py a quitte avec le code %ERR%.
    pause
)
exit /b %ERR%
