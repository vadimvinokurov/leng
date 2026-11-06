#!/usr/bin/env bash
# One-command deployment: build + start the backend container.
#
# Requirements: .env next to this script (see .env.example), docker
# (compose v2), git. Client builds happen in GitHub Actions on v* tags;
# this script never builds clients.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$root/.env" ]; then
  echo "deploy: положите .env рядом с этим скриптом (см. .env.example) и повторите" >&2
  exit 1
fi

# The server version: the exact tag this checkout stands on when there is
# one; otherwise the latest reachable release tag. Backend-only deploys
# from main (no new tag) must behave exactly like a tagged checkout: the
# gate and the release mirror key off the last client release, and a
# "dev" version here would silently disable them. Without any tag at all
# this is a dev build ("dev") and the API skips the client-version gate.
tag="$(git -C "$root" describe --tags --exact-match 2>/dev/null || true)"
if [ -z "$tag" ]; then
  tag="$(git -C "$root" describe --tags --abbrev=0 2>/dev/null || true)"
fi
version="${tag#v}"
if [ -z "$version" ]; then
  version="dev"
  echo "deploy: тегов в репозитории нет — версия сервера \"dev\" (версионный gate отключён)" >&2
fi
export LENG_VERSION="$version"

docker compose build backend
docker compose up -d backend

port="$(grep -E '^LENG_PORT=' "$root/.env" | cut -d= -f2 || true)"
port="${port:-28080}"
echo "deploy: сервер версии \"$version\" поднят:"
echo "  API:        http://localhost:$port/v1/health"
echo "  скачивание: http://localhost:$port/"
