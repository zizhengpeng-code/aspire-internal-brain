#!/usr/bin/env bash
# Run a command against a freshly started app, then tear the app down again.
#
#   CHROME_PATH="..." results/with-app.sh node results/something.mjs
#
# The whole lifecycle lives in one script on purpose: a server started as its own background shell
# dies with that shell, so "start the app" and "use the app" cannot be two separate invocations.
# The EXIT trap also reaps anything still holding the port — leaking a Streamlit child used to
# cost ~4 GB and turn a 100-second test run into a 14-minute one.
set -u
cd "$(dirname "$0")/.." || exit 1

PY="C:/Users/Administrator/AppData/Local/Programs/Python/Python314/python.exe"
PORT="${PORT:-8503}"
LOG="${LOG:-results/with-app.log}"

cleanup() {
  [ -n "${APP_PID:-}" ] && kill "$APP_PID" 2>/dev/null
  sleep 1
  for pid in $(netstat -ano | grep ":$PORT " | grep LISTENING | awk '{print $5}' | sort -u); do
    taskkill //PID "$pid" //F >/dev/null 2>&1 && echo "reaped pid $pid still on $PORT"
  done
}
trap cleanup EXIT

echo "== starting app on $PORT =="
if [ -n "${APP_FILE:-}" ]; then
  # A throwaway app (a one-off probe page), served directly — `start.py` is the deployment entry
  # point and always runs the project's own `app.py`.
  PORT=$PORT "$PY" -m streamlit run "$APP_FILE" \
    --server.port "$PORT" --server.address 127.0.0.1 --server.headless true \
    --browser.gatherUsageStats false > "$LOG" 2>&1 &
else
  PORT=$PORT "$PY" start.py > "$LOG" 2>&1 &
fi
APP_PID=$!

healthy=0
for i in $(seq 1 60); do
  # --noproxy: this box routes curl through a proxy that cannot see loopback.
  code=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' \
         "http://127.0.0.1:$PORT/_stcore/health" 2>/dev/null || true)
  if [ "$code" = "200" ]; then echo "app healthy after ${i}s"; healthy=1; break; fi
  sleep 1
done
if [ "$healthy" != "1" ]; then echo "APP NEVER CAME UP"; tail -20 "$LOG"; exit 1; fi

echo "== running: $* =="
PORT=$PORT TARGET_URL="http://127.0.0.1:$PORT" "$@"
