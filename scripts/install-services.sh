#!/bin/bash
# Install/reload the d1 decision-stack launchd services from this repo.
# Prereqs (not in git): workspace mlx-env venv with mlx-vlm, and the
# LiquidAI/d1-Omni-2B-MLX-8bit model cached under ~/.cache/huggingface.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UID_="$(id -u)"
LA=~/Library/LaunchAgents
LOGS=/Users/remote-mac/.openclaw/workspace/logs
mkdir -p "$LOGS"

for label in com.openclaw.d1-server com.openclaw.typesafe-adapter; do
  cp "$REPO/launchd/$label.plist" "$LA/$label.plist"
  launchctl bootout "gui/$UID_/$label" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "gui/$UID_" "$LA/$label.plist"
  echo "bootstrapped $label"
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
