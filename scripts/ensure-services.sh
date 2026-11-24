#!/bin/bash
# Self-heal the Kev decision stack. Idempotent and cheap: exits in ~0.3s when
# everything is healthy, and repairs it when a reboot, an app-managed update, a
# LaunchAgent reconcile pass, or a crash loop left the units unloaded or dead.
#
# Run by launchd (com.openclaw.kev-watchdog, StartInterval) and by the d1-local
# plugin on gateway startup. Also safe to run by hand.
#
#   scripts/ensure-services.sh            # heal quietly, log only on action
#   scripts/ensure-services.sh --verbose  # print to stdout as well
#
# Park the stack (stop self-healing) with: touch .kev-paused   (in repo root)
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UID_="$(id -u)"
DOMAIN="gui/$UID_"
LA="$HOME/Library/LaunchAgents"
LOGS="${KEV_LOGS:-$HOME/.openclaw/workspace/logs}"
STATE="${KEV_WATCHDOG_STATE:-$LOGS/.kev-watchdog-state}"
VERBOSE="${1:-}"

RUNNER_HEALTH="http://127.0.0.1:${KEV_RUNNER_PORT:-8090}/health"
ADAPTER_HEALTH="http://127.0.0.1:${KEV_ADAPTER_PORT:-8009}/health"
LABELS=(com.openclaw.d1-server com.openclaw.typesafe-adapter com.openclaw.kev-log-clean com.openclaw.kev-watchdog)

mkdir -p "$LOGS"
log() {
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') $*"
  printf '%s\n' "$line" >> "$LOGS/kev-watchdog.log"
  [ "$VERBOSE" = "--verbose" ] && printf '%s\n' "$line"
}

if [ -f "$REPO/.kev-paused" ]; then
  [ "$VERBOSE" = "--verbose" ] && echo "paused (.kev-paused present); nothing to do"
  exit 0
fi

PYTHON="${KEV_PYTHON:-$(
  if [ -f "$REPO/config/model.env" ]; then
    # shellcheck disable=SC1091
    . "$REPO/config/model.env" && echo "${KEV_PYTHON:-}"
  fi
)}"
PYTHON="${PYTHON:-$HOME/.openclaw/workspace/mlx-env/bin/python}"

render() { # label
  local label="$1" tmpl="$REPO/launchd/templates/$1.plist.template"
  [ -f "$tmpl" ] || { log "MISSING template $tmpl"; return 1; }
  sed -e "s|{{REPO}}|$REPO|g" -e "s|{{LOGS}}|$LOGS|g" -e "s|{{PYTHON}}|$PYTHON|g" \
    "$tmpl" > "$LA/$label.plist" || return 1
  plutil -lint "$LA/$label.plist" >/dev/null || { log "INVALID plist for $label"; return 1; }
  return 0
}

boot() { # label
  render "$1" || return 1
  launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
  sleep 1
  if launchctl bootstrap "$DOMAIN" "$LA/$1.plist" 2>/dev/null; then
    log "RECOVER bootstrapped $1"
    return 0
  fi
  log "FAILED to bootstrap $1"
  return 1
}

loaded() { launchctl print "$DOMAIN/$1" >/dev/null 2>&1; }
runner_up() { curl -fsS -m 3 "$RUNNER_HEALTH" >/dev/null 2>&1; }
adapter_up() { curl -fsS -m 3 "$ADAPTER_HEALTH" 2>/dev/null | grep -q '"upstream_ok":[[:space:]]*true'; }

# Recovery throttle: a repair is only rate-limited if the previous repair failed
# to bring the stack back (crash-loop protection). A fresh failure after a
# healthy period is repaired immediately. One allowed repair pass fixes every
# missing unit in the same run.
NOW="$(date +%s)"
LAST_REPAIR="$(sed -n 's/^last_repair=//p' "$STATE" 2>/dev/null)"
LAST_REPAIR="${LAST_REPAIR:-0}"
if [ $((NOW - LAST_REPAIR)) -lt "${KEV_REPAIR_COOLDOWN:-240}" ]; then
  ALLOW_REPAIR=0
else
  ALLOW_REPAIR=1
fi
mark_repair() { printf 'last_repair=%s\n' "$NOW" > "$STATE"; LAST_REPAIR="$NOW"; }

changed=0

# 1. Units missing from the launchd domain (the reboot/update failure mode).
for label in "${LABELS[@]}"; do
  if ! loaded "$label"; then
    if [ "$label" = "com.openclaw.d1-server" ] || [ "$label" = "com.openclaw.typesafe-adapter" ]; then
      if [ "$ALLOW_REPAIR" != 1 ]; then log "SKIP $label unloaded but repair throttled"; continue; fi
      mark_repair
    fi
    boot "$label" && changed=1
  fi
done

# 2. Loaded but not answering: kickstart the unit (bounded, one pass per run).
if loaded com.openclaw.d1-server && ! runner_up; then
  if [ "$ALLOW_REPAIR" != 1 ]; then
    log "SKIP runner unhealthy but repair throttled"
  else
    mark_repair
    launchctl kickstart -k "$DOMAIN/com.openclaw.d1-server" 2>/dev/null \
      && log "RECOVER kickstarted d1-server (health probe failed)" && changed=1
    for _ in $(seq 1 60); do runner_up && break; sleep 2; done
  fi
fi

if loaded com.openclaw.typesafe-adapter && ! adapter_up; then
  if runner_up; then
    launchctl kickstart -k "$DOMAIN/com.openclaw.typesafe-adapter" 2>/dev/null \
      && log "RECOVER kickstarted typesafe-adapter (upstream_ok false)" && changed=1
  fi
fi

# 3. Verdict. A healthy outcome clears the throttle so the next failure heals
# at once; a degraded one keeps it until the cooldown expires.
if runner_up && adapter_up; then
  rm -f "$STATE" 2>/dev/null || true
  [ "$changed" = 1 ] && log "OK stack recovered"
  [ "$VERBOSE" = "--verbose" ] && echo "healthy: runner+adapter"
  exit 0
fi
log "DEGRADED runner=$(runner_up && echo up || echo down) adapter=$(adapter_up && echo up || echo down)"
[ "$VERBOSE" = "--verbose" ] && echo "degraded; see $LOGS/kev-watchdog.log"
exit 1
