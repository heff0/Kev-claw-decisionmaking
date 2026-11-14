#!/bin/bash
# Remove the launchd units installed by install-services.sh (keeps logs + model cache).
set -uo pipefail
LA="$HOME/Library/LaunchAgents"
for n in com.openclaw.d1-server com.openclaw.typesafe-adapter com.openclaw.kev-log-clean; do
  launchctl bootout "gui/$(id -u)/$n" 2>/dev/null && echo "booted out $n"
  rm -f "$LA/$n.plist"
done
echo "units removed; logs and model cache left in place"
