@echo off
setlocal

REM ============================================================
REM  BIShift.AI / bobj-converter - manual deploy to Cloud Foundry (dev)
REM  Double-click this file, or run it from a terminal.
REM ============================================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "UI=%ROOT%frontend\public"
set "CF_API=https://api.cf.eu10-004.hana.ondemand.com"
set "CF_ORG=Crave InfoTech-workshop-sap-build-9w562br3"
set "CF_SPACE=dev"
set "PASSCODE_URL=https://login.cf.eu10-004.hana.ondemand.com/passcode"

echo ============================================================
echo   BIShift.AI - Deploy to Cloud Foundry [%CF_SPACE%]
echo ============================================================
echo.

REM --- 1. Ensure authenticated --------------------------------
cf oauth-token >nul 2>&1
if errorlevel 1 (
  echo Not logged in, or the token has expired.
  echo Open this URL, copy the one-time passcode, and paste it when prompted:
  echo   %PASSCODE_URL%
  echo.
  cf api %CF_API%
  cf login --sso
  if errorlevel 1 (
    echo.
    echo Login failed. Aborting.
    exit /b 1
  )
)

REM --- 2. Target the right org / space ------------------------
cf target -o "%CF_ORG%" -s "%CF_SPACE%" >nul 2>&1
if errorlevel 1 (
  echo Could not target the org / space. Aborting.
  exit /b 1
)
echo Target: %CF_ORG% / %CF_SPACE%
echo.

REM --- 3. Deploy backend API ----------------------------------
echo [1/2] Deploying backend API - bobj-converter-api-dev ...
pushd "%BACKEND%"
cf push bobj-converter-api-dev --no-manifest -b python_buildpack -m 512M -k 512M -c "gunicorn app.main:app -w 1 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT"
set "RC=%errorlevel%"
popd
if not "%RC%"=="0" (
  echo Backend deploy FAILED [exit %RC%]. Aborting.
  exit /b %RC%
)
echo Backend deployed OK.
echo.

REM --- 4. Deploy frontend UI ----------------------------------
echo [2/2] Deploying frontend UI - bobj-converter-ui ...
cf push bobj-converter-ui --no-manifest -b staticfile_buildpack -m 64M -p "%UI%"
if errorlevel 1 (
  echo UI deploy FAILED.
  exit /b 1
)
echo UI deployed OK.
echo.

echo ============================================================
echo   Deploy complete.
echo   API : https://crave-bobj-sac-convertor-api.cfapps.eu10-004.hana.ondemand.com/api/health
echo   UI  : https://crave-bw-bobj-datasphere-convertor.cfapps.eu10-004.hana.ondemand.com
echo ============================================================
echo.
pause
endlocal
