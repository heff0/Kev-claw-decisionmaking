#!/bin/bash
# Daily sync: commit local changes and push to GitHub (pull --rebase first).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${KEV_SYNC_LOG:-$REPO/logs/git-sync.log}"
mkdir -p "$(dirname "$LOG")"
cd "$REPO" || exit 1
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -m "chore: daily auto-sync $(date '+%Y-%m-%d')" && echo "committed local changes"
  else
    echo "no local changes"
  fi
  git pull --rebase origin main && git push origin main && echo "SYNC OK"
} >> "$LOG" 2>&1
