@echo off
rem One-command deployment (Windows cmd): build + start the backend
rem container.
rem
rem Requirements: .env next to this script (see .env.example), Docker
rem Desktop, git. Client builds happen in GitHub Actions on v* tags; this
rem script never builds clients. Messages are English on purpose: cmd
rem console code pages cannot reliably render Cyrillic.
setlocal enabledelayedexpansion

if not exist "%~dp0.env" (
  echo deploy: put .env next to this script ^(see .env.example^), then rerun.
  exit /b 1
)

rem The server version: the exact tag of this checkout without the
rem leading "v"; without a tag this is a dev build.
set "TAG="
for /f "usebackq delims=" %%i in (`git -C "%~dp0." describe --tags --exact-match 2^>nul`) do set "TAG=%%i"
set "LENG_VERSION=dev"
if defined TAG set "LENG_VERSION=%TAG:~1%"
if not defined TAG (
  echo deploy: no exact tag found - server version "dev" ^(version gate off^)
)
set LENG_VERSION=!LENG_VERSION!

docker compose build backend
docker compose up -d backend

set "PORT=28080"
for /f "usebackq tokens=2 delims==" %%p in (`findstr /b "LENG_PORT=" "%~dp0.env"`) do set "PORT=%%p"

echo deploy: server version "!LENG_VERSION!" is up:
echo   API:        http://localhost:!PORT!/v1/health
echo   downloads:  http://localhost:!PORT!/
endlocal
