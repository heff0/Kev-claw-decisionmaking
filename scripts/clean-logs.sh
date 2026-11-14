#!/bin/zsh
# Clean/rotate Kev decision logs. Runs daily via launchd
# (com.openclaw.kev-log-clean). Safe to run any time.
#
# Policy:
#  - logs/usage.jsonl      -> prune records older than KEEP_DAYS (default 90),
#                             gzip any rotated copies older than 7 days
#  - launchd stdout/err    -> truncate in place once any exceeds MAX_MB (default 10)
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
WS_LOGS="${KEV_LOGS:-$HOME/.openclaw/workspace/logs}"
KEEP_DAYS="${KEEP_DAYS:-90}"
MAX_MB="${MAX_MB:-10}"

usage="$REPO/logs/usage.jsonl"

# 1) Prune usage.jsonl by timestamp (each line has "ts": <epoch>).
if [[ -f "$usage" ]]; then
  cutoff=$(( $(date +%s) - KEEP_DAYS * 86400 ))
  tmp="$usage.tmp.$$"
  # Keep lines without ts (conservative) plus lines newer than cutoff.
  # Portable awk (macOS BWK): extract the ts value via RSTART/RLENGTH.
  /usr/bin/awk -v cutoff="$cutoff" '
    {
      if (match($0, /"ts":[ ]*[0-9]+/)) {
        n = substr($0, RSTART + 5, RLENGTH - 5) + 0
        if (n >= cutoff) print
      } else { print }
    }
  ' "$usage" > "$tmp"
  if [[ $(wc -l < "$tmp") -le $(wc -l < "$usage") ]]; then
    mv "$tmp" "$usage"
  else
    rm -f "$tmp"
  fi
fi

# 2) Gzip rotated usage copies older than 7 days.
find "$REPO/logs" -maxdepth 1 -name 'usage.jsonl.*' -mtime +7 ! -name '*.gz' \
  -exec gzip -q {} \; 2>/dev/null || true

# 3) Truncate oversized launchd logs (in place; launchd holds file handles,
#    so truncate rather than delete-and-recreate).
for f in "$WS_LOGS/d1-server.log" "$WS_LOGS/d1-server.err.log" \
         "$WS_LOGS/typesafe-adapter.log" "$WS_LOGS/typesafe-adapter.err.log"; do
  [[ -f "$f" ]] || continue
  size=$(stat -f %z "$f")
  if (( size > MAX_MB * 1024 * 1024 )); then
    # Keep the tail 1 MB, drop the head.
    tail -c 1048576 "$f" > "$f.cut" && cat "$f.cut" > "$f" && rm -f "$f.cut"
    echo "$(date '+%F %T') truncated $f (was ${size} bytes)"
  fi
done

echo "$(date '+%F %T') kev log clean done (usage keep=${KEEP_DAYS}d, launchd max=${MAX_MB}MB)"
