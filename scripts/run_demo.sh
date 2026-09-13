#!/usr/bin/env bash
# Start the full Prahari stack for a demonstration.
#
#   scripts/run_demo.sh           start core + edge, fresh database
#   scripts/run_demo.sh --keep    keep the existing database and evidence
#   scripts/run_demo.sh --stop    stop everything
#
# Ports are 8420 (edge) and 9420 (core) rather than 8000/9000 because Windows
# hosts running Hyper-V/WSL reserve large TCP ranges - commonly 7986-8185 - and
# binding inside one fails with WinError 10013, which reads like a permissions
# problem and wastes an afternoon.
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

EDGE_PORT="${PRAHARI_PORT:-8420}"
CORE_PORT="${PRAHARI_CORE_PORT:-9420}"

if [[ -x ".venv/Scripts/python.exe" ]]; then
  PY=".venv/Scripts/python.exe"          # Windows
elif [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"                  # Linux / macOS
else
  echo "No virtualenv found. Create one first:"
  echo "    python -m venv --system-site-packages .venv"
  echo "    .venv/Scripts/python.exe -m pip install -r requirements.txt"
  exit 1
fi

stop_all() {
  echo "Stopping Prahari..."
  for f in var/edge.pid var/core.pid; do
    [[ -f "$f" ]] || continue
    pid="$(cat "$f")"
    kill "$pid" 2>/dev/null || taskkill //F //PID "$pid" >/dev/null 2>&1
    rm -f "$f"
  done
  echo "Stopped."
}

case "${1:-}" in
  --stop) stop_all; exit 0 ;;
  --keep) KEEP=1 ;;
  *)      KEEP=0 ;;
esac

stop_all >/dev/null 2>&1
mkdir -p var

if [[ "$KEEP" -eq 0 ]]; then
  echo "Clearing previous database and evidence..."
  rm -f var/prahari-edge.db* var/prahari-core.db*
  rm -rf var/evidence var/core-evidence
fi

echo "Starting sector core on :$CORE_PORT ..."
"$PY" -m uvicorn prahari.core.app:app --host 127.0.0.1 --port "$CORE_PORT" \
      --log-level warning > var/core.log 2>&1 &
echo $! > var/core.pid
sleep 4

echo "Starting edge node on :$EDGE_PORT ..."
PRAHARI_CORE_URL="http://127.0.0.1:$CORE_PORT" \
  "$PY" -m uvicorn prahari.edge.app:app --host 127.0.0.1 --port "$EDGE_PORT" \
        --log-level warning > var/startup.log 2>&1 &
echo $! > var/edge.pid

echo -n "Waiting for the node to come up"
for _ in $(seq 1 40); do
  if curl -s --max-time 2 "http://127.0.0.1:$EDGE_PORT/health" >/dev/null 2>&1; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 2
done

PW="$(grep -oE 'password: \S+' var/startup.log 2>/dev/null | awk '{print $2}' | tail -1)"
if [[ -n "$PW" ]]; then
  echo "$PW" > var/.demo_admin_pw
fi

cat <<BANNER

  ────────────────────────────────────────────────────────────
   PRAHARI is running

     Dashboard    http://127.0.0.1:$EDGE_PORT
     API docs     http://127.0.0.1:$EDGE_PORT/docs
     Sector core  http://127.0.0.1:$CORE_PORT

     Username     admin
     Password     ${PW:-(see var/startup.log)}

   Give it about two minutes before presenting: cameras refuse
   to certify themselves until they have observed enough
   pedestrians to fit their ground plane. That wait is the
   feature, not a delay.

   Logs     var/startup.log   var/core.log
   Stop     scripts/run_demo.sh --stop
  ────────────────────────────────────────────────────────────

BANNER
