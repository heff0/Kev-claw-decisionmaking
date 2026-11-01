#!/bin/bash
# launchd entrypoint for the System One runner: sources config/model.env so the
# model choice lives in one operator-editable file, then execs the runner.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [ -f "$REPO/config/model.env" ]; then
  # shellcheck disable=SC1091
  set -a; . "$REPO/config/model.env"; set +a
fi

PYTHON="${KEV_PYTHON:-$HOME/.openclaw/workspace/mlx-env/bin/python}"
exec "$PYTHON" "$REPO/services/d1-systemone-server.py"
