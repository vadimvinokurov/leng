#!/usr/bin/env bash
# Golden-path e2e: build the image, start backend + fake-llm under compose,
# query a word twice over HTTP, and assert the SQLite state (one word
# queries row, then occurrences == 2 on the word entry). Non-zero exit = red.
# Requires bash (set -o pipefail, $RANDOM).
# Hermetic: DB/data live in a temp dir behind LENG_DATA_DIR; the temp .env
# is written to the repo root only because compose resolves `env_file: .env`
# relative to the compose file, and is removed on exit. Because of that it
# REFUSES to run while a .env exists in the repo root: it must not clobber
# a developer's own environment file.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -e .env ]; then
  echo "golden.sh must not run while .env exists - it manages its own temp env" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "golden.sh: docker daemon unavailable" >&2
  exit 2
fi

port=$((18080 + RANDOM % 4000))
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/leng-golden.XXXXXX")"
data_dir="$work_dir/data"
mkdir -p "$data_dir"

cleanup() {
  docker compose --profile test down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f .env
  rm -rf "$work_dir"
}
trap cleanup EXIT

cat > .env <<EOF
LENG_API_TOKEN=test-token
LENG_DB_PATH=/data/leng.db
LENG_LLM_BASE_URL=http://fake-llm:8000
LENG_LLM_API_KEY=test-key
LENG_LLM_TIMEOUT_S=10
LENG_PORT=$port
LENG_DATA_DIR=$data_dir
EOF

base_url="http://localhost:$port"

echo "== build backend image =="
docker compose build backend

echo "== compose up (backend + fake-llm) =="
docker compose --profile test up -d

echo "== wait for $base_url/v1/health =="
healthy=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if curl -fsS "$base_url/v1/health" >/dev/null 2>&1; then
    healthy=yes
    break
  fi
  sleep 1
done
[ -n "$healthy" ] || { echo "golden.sh: backend never became healthy" >&2; exit 1; }
curl -fsS "$base_url/v1/health"; echo

post_query() {
  curl -fsS -X POST "$base_url/v1/query" \
    -H "X-Api-Token: test-token" \
    -H "Content-Type: application/json" \
    -d '{"raw_text":"throughput","source":{"app":"golden"},"client":"golden.sh"}'
}

echo "== first query =="
first="$(post_query)"
echo "$first"
echo "$first" | grep -q '"kind":"word"' \
  || { echo "golden.sh: first query kind != word" >&2; exit 1; }

echo "== DB check #1: exactly one word queries row =="
docker compose exec -T backend python -c '
import sqlite3
conn = sqlite3.connect("/data/leng.db")
rows = conn.execute("SELECT kind, raw_text FROM queries").fetchall()
assert rows == [("word", "throughput")], rows
print("queries rows:", rows)
'

echo "== second query =="
second="$(post_query)"
echo "$second"
echo "$second" | grep -q '"kind":"word"' \
  || { echo "golden.sh: second query kind != word" >&2; exit 1; }

echo "== DB check #2: occurrences == 2 =="
docker compose exec -T backend python -c '
import sqlite3
conn = sqlite3.connect("/data/leng.db")
count = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
assert count == 2, count
rows = conn.execute("SELECT canonical, occurrences FROM word_entries").fetchall()
assert rows == [("throughput", 2)], rows
print("word_entries:", rows)
'

echo "GOLDEN PATH PASSED"
