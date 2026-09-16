@echo off
title Modish - server update
setlocal
set SERVER=root@194.87.118.236
set BASE=%~dp0

echo.
echo  ===============================================
echo   Modish: deploy backend update to VPS (v3)
echo  ===============================================
echo.
echo  You will be asked for the server password TWICE.
echo.

set STAGE=%TEMP%\modish_update
set ARCHIVE=%TEMP%\modish_update.tgz
if exist "%STAGE%" rmdir /s /q "%STAGE%"
if exist "%ARCHIVE%" del /q "%ARCHIVE%"
mkdir "%STAGE%"

echo  [0/2] Preparing files...
robocopy "%BASE%backend" "%STAGE%\backend" /E /NFL /NDL /NJH /NJS /XD .venv __pycache__ .pytest_cache test_feeds /XF .env
if errorlevel 8 goto :prepfail
copy /y "%BASE%deploy\server_update.sh" "%STAGE%\" >nul

tar -czf "%ARCHIVE%" -C "%STAGE%" .
if errorlevel 1 goto :prepfail

echo  [1/2] Uploading archive to server...
scp -o StrictHostKeyChecking=accept-new -q "%ARCHIVE%" %SERVER%:/tmp/modish_update.tgz
if errorlevel 1 goto :scperr

echo  [2/2] Updating server (clean unpack + backup + rebuild + catalog refresh)...
ssh -o StrictHostKeyChecking=accept-new %SERVER% "rm -rf /tmp/modish_update && mkdir -p /tmp/modish_update && tar -xzf /tmp/modish_update.tgz -C /tmp/modish_update && rm -f /tmp/modish_update.tgz && tr -d '\r' < /tmp/modish_update/server_update.sh > /tmp/server_update.sh && bash /tmp/server_update.sh"
if errorlevel 1 goto :ssherr

rmdir /s /q "%STAGE%"
del /q "%ARCHIVE%"
echo.
echo  ALL DONE! The app now uses the new recommendation engine.
echo  Open the app, pass onboarding / tap some likes and check the feed.
echo.
pause
exit /b 0

:prepfail
echo  ERROR: failed to prepare files.
pause
exit /b 1

:scperr
echo  ERROR: upload failed. Check password / internet connection.
pause
exit /b 1

:ssherr
echo  ERROR: update script failed. Copy the text above and send it to Claude.
pause
exit /b 1
