@echo off
echo ============================================
echo   Proctor Browser - Cleanup Tool
echo ============================================
echo.

echo Stopping all Proctor Browser processes...
taskkill /F /IM "Proctor Browser.exe" 2>nul
taskkill /F /IM "ProctorBrowser.exe" 2>nul
taskkill /F /IM "electron.exe" 2>nul
taskkill /F /IM "python.exe" 2>nul
taskkill /F /IM "python3.exe" 2>nul
timeout /t 2 /nobreak >nul

echo Removing installation files...
set "INSTALL=%LOCALAPPDATA%\Programs\Proctor Browser"
if exist "%INSTALL%" (
    rmdir /s /q "%INSTALL%"
    echo   Removed: %INSTALL%
) else (
    echo   Not found at default location, scanning...
    for /d %%D in ("%LOCALAPPDATA%\Programs\*Proctor*") do (
        rmdir /s /q "%%D"
        echo   Removed: %%D
    )
)

echo Removing app data...
set "APPDATA_DIR=%APPDATA%\Proctor Browser"
if exist "%APPDATA_DIR%" (
    rmdir /s /q "%APPDATA_DIR%"
    echo   Removed app data
)

set "LOCAL_DIR=%LOCALAPPDATA%\Proctor Browser"
if exist "%LOCAL_DIR%" (
    rmdir /s /q "%LOCAL_DIR%"
    echo   Removed local data
)

echo Removing shortcuts...
del "%USERPROFILE%\Desktop\Proctor Browser.lnk" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Proctor Browser.lnk" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Proctor Browser.lnk" 2>nul

echo.
echo ============================================
echo   Done! Proctor Browser has been removed.
echo ============================================
echo.
pause
