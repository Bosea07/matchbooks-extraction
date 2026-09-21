@echo off
REM Runs the whole suite. Double-click it, or run it from anywhere - it does
REM not care what the current directory is, which is the point.
cd /d "%~dp0"

echo ============================================================
echo  matchbooks-extraction - test suite
echo  %CD%
echo ============================================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo ERROR: the "py" launcher was not found.
  echo Install Python from python.org and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

set FAILED=0

for %%F in (
  tests\test_allocation_and_signs.py
  tests\test_sap_ledger.py
  tests\test_reference_identity.py
  tests\test_currency_and_types.py
  tests\test_unreferenced_rows.py
) do (
  echo ------------------------------------------------------------
  echo  %%F
  echo ------------------------------------------------------------
  py -B "%%F"
  if errorlevel 1 (
    echo   ^>^> FAILED
    set FAILED=1
  )
  echo.
)

echo ============================================================
if "%FAILED%"=="0" (
  echo  ALL SUITES PASSED - safe to commit and push
) else (
  echo  SOMETHING FAILED - copy the traceback above before pushing
)
echo ============================================================
echo.
pause
