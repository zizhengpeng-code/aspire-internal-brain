#!/usr/bin/env bash
# Ask the running app directly whether the brand assets are actually being served, instead of
# inferring it from a screenshot. Prints the status and content-type for each URL.
set -u
cd "$(dirname "$0")/.." || exit 1

PY="C:/Users/Administrator/AppData/Local/Programs/Python/Python314/python.exe"
PORT="${PORT:-8502}"

cleanup() {
  [ -n "${APP_PID:-}" ] && kill "$APP_PID" 2>/dev/null
  sleep 1
  for pid in $(netstat -ano | grep ":$PORT " | grep LISTENING | awk '{print $5}' | sort -u); do
    taskkill //PID "$pid" //F >/dev/null 2>&1
  done
}
trap cleanup EXIT

PORT=$PORT "$PY" start.py > results/check-assets-app.log 2>&1 &
APP_PID=$!
for i in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' "http://127.0.0.1:$PORT/_stcore/health" || true)" = "200" ] && break
  sleep 1
done

for path in \
  "/app/static/fonts/jost-latin.woff2" \
  "/app/static/brand/aspire-lockup.png" \
  "/app/static/brand/aspire-mark.png" \
  "/app/static/brand/favicon.png" \
  "/static/fonts/jost-latin.woff2" \
  "/fonts/jost-latin.woff2" ; do
  read -r code type size < <(curl -s -o /dev/null --noproxy '*' \
      -w '%{http_code} %{content_type} %{size_download}' "http://127.0.0.1:$PORT$path")
  printf '%-38s %s  %-28s %s bytes\n' "$path" "$code" "$type" "$size"
done
