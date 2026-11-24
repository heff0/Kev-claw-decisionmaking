#!/bin/bash
# Read-only health report for the Kev decision stack. Never repairs anything.
# Exit 0 = healthy, 1 = degraded. Intended for humans, cron, and dashboards.
set -uo pipefail
UID_="$(id -u)"
DOMAIN="gui/$UID_"
RUNNER_PORT="${KEV_RUNNER_PORT:-8090}"
ADAPTER_PORT="${KEV_ADAPTER_PORT:-8009}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="${KEV_LOGS:-$HOME/.openclaw/workspace/logs}"

fail=0
row() { printf '%-26s %s\n' "$1" "$2"; }

printf 'Kev decision stack — %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"

for label in com.openclaw.d1-server com.openclaw.typesafe-adapter com.openclaw.kev-log-clean com.openclaw.kev-watchdog; do
  info="$(launchctl print "$DOMAIN/$label" 2>/dev/null)"
  if [ -z "$info" ]; then
    row "$label" "NOT LOADED"
    [ "$label" != "com.openclaw.kev-log-clean" ] && fail=1
  else
    pid="$(printf '%s' "$info" | sed -n 's/^[[:space:]]*pid = \([0-9]*\).*/\1/p' | head -1)"
    row "$label" "loaded pid=${pid:-none}"
  fi
done

runner="$(curl -fsS -m 3 "http://127.0.0.1:$RUNNER_PORT/health" 2>/dev/null)"
if [ -n "$runner" ]; then
  row "runner :$RUNNER_PORT" "$runner"
else
  row "runner :$RUNNER_PORT" "DOWN"; fail=1
fi
adapter="$(curl -fsS -m 3 "http://127.0.0.1:$ADAPTER_PORT/health" 2>/dev/null)"
if printf '%s' "$adapter" | grep -q '"upstream_ok":[[:space:]]*true'; then
  row "adapter :$ADAPTER_PORT" "$adapter"
else
  row "adapter :$ADAPTER_PORT" "${adapter:-DOWN}"; fail=1
fi

if [ -f "$REPO/.kev-paused" ]; then row "self-heal" "PAUSED (.kev-paused)"; fi
if [ -f "$LOGS/kev-watchdog.log" ]; then
  row "watchdog log (last 3)" ""
  tail -3 "$LOGS/kev-watchdog.log" | sed 's/^/  /'
fi
tail -1 "$REPO/logs/usage.jsonl" 2>/dev/null | sed 's/^/last decision: /'
exit "$fail"
