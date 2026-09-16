@echo off
title Modish - recommendation check
cd /d "%~dp0backend"
set PYTHONPATH=%CD%
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found in backend folder
  pause
  exit /b 1
)

echo.
echo [1/2] Running tests, please wait 1-2 minutes...
".venv\Scripts\python.exe" tests\test_recommendation_v3.py -v > "..\result_tests.txt" 2>&1
".venv\Scripts\python.exe" tests\test_catalog_import.py -v >> "..\result_tests.txt" 2>&1
".venv\Scripts\python.exe" tests\test_admitad_csv_import.py -v >> "..\result_tests.txt" 2>&1
".venv\Scripts\python.exe" tests\test_feed_filters.py -v >> "..\result_tests.txt" 2>&1
".venv\Scripts\python.exe" tests\test_gender_filters.py -v >> "..\result_tests.txt" 2>&1
echo       Done. See result_tests.txt

echo [2/2] Building demo feed from real catalog...
".venv\Scripts\python.exe" scripts\demo_feed.py > "..\demo_run.txt" 2>&1
echo       Done. See demo_lenta.txt

echo.
echo FINISHED. Result files are in the modish folder:
echo   - result_tests.txt
echo   - demo_lenta.txt
echo.
pause
