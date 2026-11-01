#!/bin/bash
# Install/reload the d1 decision-stack launchd services from this repo.
# Renders launchd/templates/*.template with this machine's paths, then
# bootstraps the units. Idempotent: safe to re-run any time.
#
# Prereqs: python env with mlx-vlm (scripts/setup.sh provisions one) and the
# model from config/model.env cached under ~/.cache/huggingface.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UID_="$(id -u)"
LA="$HOME/Library/LaunchAgents"
LOGS="${KEV_LOGS:-$HOME/.openclaw/workspace/logs}"
PYTHON="${KEV_PYTHON:-$(
  if [ -f "$REPO/config/model.env" ]; then
    # shellcheck disable=SC1091
    . "$REPO/config/model.env" && echo "${KEV_PYTHON:-}"
  fi
)}"
PYTHON="${PYTHON:-$HOME/.openclaw/workspace/mlx-env/bin/python}"

# First run: create config/model.env from the example if missing.
if [ ! -f "$REPO/config/model.env" ] && [ -f "$REPO/config/model.env.example" ]; then
  sed "s|{{PYTHON}}|$PYTHON|" "$REPO/config/model.env.example" > "$REPO/config/model.env"
  echo "created config/model.env from model.env.example (KEV_PYTHON=$PYTHON)"
fi

mkdir -p "$LOGS"

for label in com.openclaw.d1-server com.openclaw.typesafe-adapter com.openclaw.kev-log-clean; do
  tmpl="$REPO/launchd/templates/$label.plist.template"
  [ -f "$tmpl" ] || { echo "missing template $tmpl" >&2; exit 1; }
  sed -e "s|{{REPO}}|$REPO|g" \
      -e "s|{{LOGS}}|$LOGS|g" \
      -e "s|{{PYTHON}}|$PYTHON|g" \
      "$tmpl" > "$LA/$label.plist"
  plutil -lint "$LA/$label.plist" >/dev/null
  launchctl bootout "gui/$UID_/$label" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "gui/$UID_" "$LA/$label.plist"
  echo "bootstrapped $label (repo=$REPO logs=$LOGS)"
done

echo "waiting for runner health..."
for i in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8090/health >/dev/null 2>&1; then
    echo "runner healthy:"; curl -s http://127.0.0.1:8090/health; echo
    exit 0
  fi
  sleep 2
done
echo "runner did not become healthy in 180s; check $LOGS/d1-server.err.log" >&2
exit 1
