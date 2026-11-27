#!/bin/sh
# Re-absorb (or verify) the vendored @openclaw/typesafe plugin copy.
#
#   scripts/sync-typesafe.sh          copy installed -> vendor/typesafe, git-diff the change, refresh DIST_SHA256
#   scripts/sync-typesafe.sh --check  compare installed vs vendor without writing (exit 1 on drift)
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${OPENCLAW_TYPESAFE_DIR:-$HOME/.openclaw/extensions/typesafe}"
DEST="$REPO/vendor/typesafe"
[ -d "$SRC/dist" ] || { echo "typesafe install not found at $SRC" >&2; exit 2; }

if [ "${1:-}" = "--check" ]; then
  tmp="$(mktemp -d)"
  for f in dist openclaw.plugin.json package.json README.md LICENSE assets; do
    [ -e "$SRC/$f" ] && cp -R "$SRC/$f" "$tmp/$f"
  done
  if diff -r -q "$tmp" "$DEST" --exclude README.md --exclude DIST_SHA256 >/dev/null 2>&1; then
    echo "vendor/typesafe matches installed plugin (no drift)"; rm -rf "$tmp"; exit 0
  else
    echo "DRIFT: installed plugin differs from vendored copy:"; diff -r -q "$tmp" "$DEST" --exclude README.md --exclude DIST_SHA256 || true; rm -rf "$tmp"; exit 1
  fi
fi

for f in dist openclaw.plugin.json package.json README.md LICENSE assets; do
  [ -e "$SRC/$f" ] || continue
  rm -rf "$DEST/$f"
  cp -R "$SRC/$f" "$DEST/$f"
done
shasum -a 256 "$DEST/dist/index.js" > "$DEST/DIST_SHA256"
cd "$REPO" && git add vendor/typesafe && git diff --cached --stat vendor/typesafe || true
echo "re-absorbed @openclaw/typesafe from $SRC — review diff, re-run scripts/contract-test.py, then commit."
