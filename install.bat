@echo off
chcp 65001 >nul 2>&1
title QoderWork Dynamic Island Installer
echo.
echo ============================================
echo   QoderWork Dynamic Island - Installer
echo ============================================
echo.

:: Set paths
set "ISLAND_DIR=%APPDATA%\QoderWork\Island"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "DESKTOP=%USERPROFILE%\Desktop"
set "SCRIPT_DIR=%~dp0"

:: Step 1: Copy exe (auto-detect: same dir, or dist\ subdir)
echo [1/4] Copying QoderWork_Island.exe ...
if not exist "%ISLAND_DIR%" mkdir "%ISLAND_DIR%"
if exist "%SCRIPT_DIR%QoderWork_Island.exe" (
    set "SRC_EXE=%SCRIPT_DIR%QoderWork_Island.exe"
) else (
    set "SRC_EXE=%SCRIPT_DIR%dist\QoderWork_Island.exe"
)
copy /Y "%SRC_EXE%" "%ISLAND_DIR%\QoderWork_Island.exe" >nul
copy /Y "%SCRIPT_DIR%island.ico" "%ISLAND_DIR%\island.ico" >nul
echo       Done.

:: Step 2: Install guardian script
echo [2/4] Installing guardian script ...
copy /Y "%SCRIPT_DIR%QoderWork_Island_Guardian.vbs" "%STARTUP_DIR%\QoderWork_Island_Guardian.vbs" >nul
echo       Done.

:: Step 3: Create desktop shortcut (dedicated VBS, UTF-8 BOM, no Chinese encoding issue)
echo [3/4] Creating desktop shortcut ...
cscript //nologo "%SCRIPT_DIR%create_shortcut.vbs"
echo       Done.

:: Step 4: Launch
echo [4/4] Launching Dynamic Island ...
start "" "%ISLAND_DIR%\QoderWork_Island.exe"
echo       Done.

echo.
echo ============================================
echo   Installation complete!
echo   The Dynamic Island should now be running.
echo ============================================
echo.
echo Press any key to exit...
pause >nul
