#!/bin/bash
# One-command setup for a fresh Apple Silicon Mac:
#   1. create (or reuse) a Python venv with pinned MLX deps
#   2. download the default decision checkpoint
#   3. create config/model.env from the example
#   4. render + bootstrap the launchd units and health-check
#
# Override with env: KEV_VENV=/path/to/venv KEV_MODEL=<hf-repo-id>
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${KEV_VENV:-$HOME/.openclaw/workspace/mlx-env}"
MODEL="${KEV_MODEL:-LiquidAI/d1-Omni-2B-MLX-8bit}"

# --- platform gate ---------------------------------------------------------
if [ "$(uname)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "This stack needs an Apple Silicon Mac (MLX + launchd)." >&2
  echo "For other platforms use the upstream typesafe hosted path instead." >&2
  exit 1
fi

# --- 1) venv with pinned deps ----------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "creating venv at $VENV"
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$REPO/services/requirements.txt"
echo "deps installed into $VENV"

# --- 2) model checkpoint ----------------------------------------------------
echo "downloading $MODEL (huggingface cache)..."
"$VENV/bin/python" -m huggingface_hub.commands.huggingface_cli download "$MODEL" 2>/dev/null \
  || "$VENV/bin/hf" download "$MODEL"

# --- 3) + 4) config + services ----------------------------------------------
KEV_PYTHON="$VENV/bin/python" "$REPO/scripts/install-services.sh"

cat <<EOF

Done. Decision stack is up:
  runner   http://127.0.0.1:8090/v1/decisions   (model: $MODEL)
  bridge   http://127.0.0.1:8009/v1/systemone   (typesafe-compatible)
Next: wire the plugin into openclaw.json (see README "Wire it up").
EOF
