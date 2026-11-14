#!/bin/bash
# Swap the decision model without an agent: set-model.sh <huggingface-repo-id>
# Validates on Hugging Face, downloads, updates config/model.env, restarts the
# launchd runner, and verifies /health reports the new model.
#
# No args: show current model and switchable cached models.
# --rollback: switch back to the previous model (kept in config/model.env.prev)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENVF="$REPO/config/model.env"
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
LABEL=com.openclaw.d1-server
UID_="$(id -u)"

get_current() { grep -m1 '^KEV_MODEL=' "$ENVF" | cut -d= -f2-; }

if [ "${1:-}" = "--rollback" ]; then
  [ -f "$REPO/config/model.env.prev" ] || { echo "no previous model recorded" >&2; exit 1; }
  PREV="$(grep -m1 '^KEV_MODEL=' "$REPO/config/model.env.prev" | cut -d= -f2-)"
  echo "rolling back to $PREV"
  new="$PREV"
elif [ -z "${1:-}" ]; then
  echo "current model: $(get_current)"
  echo "cached on disk:"
  for hub in "$HF_CACHE/hub" "$HF_CACHE"; do
    [ -d "$hub" ] || continue
    find "$hub" -maxdepth 1 -type d -name 'models--*' 2>/dev/null | sed 's|.*models--||; s|--|/|' | sed 's|^|  hf.co/|' | sort -u | head -20
    break
  done
  echo
  echo "usage: $0 <huggingface-repo-id> | --rollback"
  exit 0
else
  new="$1"
fi

# 1. Validate repo id exists and is usable with MLX naming conventions.
meta="$(curl -fsS "https://huggingface.co/api/models/$new" 2>/dev/null)" \
  || { echo "ERROR: '$new' not found on huggingface.co (check the repo id)" >&2; exit 1; }
echo "hf repo ok: $(echo "$meta" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("id","?"), "· downloads:", d.get("downloads","?"))')"

# 2. Download (fast if already cached).
PYTHON="${KEV_PYTHON:-$HOME/.openclaw/workspace/mlx-env/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
echo "downloading $new (via huggingface_hub)..."
"$PYTHON" - "$new" <<'PY'
import sys
from huggingface_hub import snapshot_download
p = snapshot_download(sys.argv[1])
print("cached at", p)
PY

# 3. Record previous, write new env.
[ -f "$ENVF" ] && grep -m1 '^KEV_MODEL=' "$ENVF" > "$REPO/config/model.env.prev" || true
if grep -q '^KEV_MODEL=' "$ENVF" 2>/dev/null; then
  sed -i '' "s|^KEV_MODEL=.*|KEV_MODEL=$new|" "$ENVF"
else
  echo "KEV_MODEL=$new" >> "$ENVF"
fi
echo "config/model.env -> KEV_MODEL=$new"

# 4. Restart the runner (launchd supervisor; kickstart works even if not yet bootstrapped state).
if launchctl print "gui/$UID_/$LABEL" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$UID_/$LABEL"
  echo "restart requested via launchctl kickstart"
else
  echo "WARNING: $LABEL is not loaded in launchd; run scripts/install-services.sh first" >&2
  exit 1
fi

# 5. Wait for health reporting the new model (preload can take ~1-2 min).
echo -n "waiting for model load"
for i in $(seq 1 120); do
  if h="$(curl -fsS http://127.0.0.1:8090/health 2>/dev/null)" && echo "$h" | grep -q "\"model\": *\"$new\""; then
    echo; echo "healthy: $h"
    echo "done. decisionModel selection in openclaw.json does not need to change (labels only)."
    exit 0
  fi
  echo -n .; sleep 2
done
echo
echo "ERROR: runner did not report model $new within 240s; check workspace logs/d1-server.err.log" >&2
exit 1
