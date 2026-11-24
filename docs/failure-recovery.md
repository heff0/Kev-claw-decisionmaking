# Failure recovery runbook

The decision stack is three launchd units plus the plugin that talks to them:

```
OpenClaw decision provider ──> d1-typesafe-adapter :8009 ──> d1-systemone-server :8090 (model resident)
   (d1-local plugin)             com.openclaw.typesafe-adapter   com.openclaw.d1-server
                                 com.openclaw.kev-log-clean      (log rotation)
                                 com.openclaw.kev-watchdog       (self-heal, every 300 s)
```

## What is self-healing now

| Layer | Trigger | Action |
| --- | --- | --- |
| `com.openclaw.kev-watchdog` | boot + every 300 s | `scripts/ensure-services.sh`: re-bootstraps any unit missing from the launchd `gui/$UID` domain, kickstarts units that are loaded but failing health checks. Repairs are throttled to one per 4 min. |
| d1-local plugin | gateway startup | spawns `ensure-services.sh` (so a dropped unit comes back even if launchd never re-loaded it) |
| d1-local plugin | 3 consecutive transport failures | circuit breaker: fail fast (`unavailable/overloaded`) for 60 s instead of paying the full `timeoutMs` per decision, triggers self-heal, keeps a half-open health probe |
| adapter | upstream refuses connection | one retry after 1 s (covers a launchd respawn in progress), then a visible `ERROR` log line |
| runner | model load fails at boot | tries the local HF cache first (works offline), then the hub, with 3 attempts and 5 s/25 s backoff instead of an instant crash-respawn loop |

## Check it

```bash
scripts/kev-status.sh          # read-only report; exit 1 if degraded
scripts/ensure-services.sh --verbose   # heal now
tail -20 ~/.openclaw/workspace/logs/kev-watchdog.log
```

Healthy looks like: all four labels loaded with pids, runner `{"ok":true,...}`,
adapter `{"ok":true,"upstream_ok":true,...}`.

## Park the stack

```bash
touch .kev-paused     # in the repo root: watchdog and plugin stop healing
rm .kev-paused
```

## Known failure mode this was built for

After the 2026-11-23 reboot the runner had started normally (it logged a warmup
at 17:45) yet by 21:37 both `com.openclaw.d1-server` and
`com.openclaw.typesafe-adapter` were gone from the launchd domain — no crash
trace in either service log, so they were booted out by something external
(an app-managed update / LaunchAgent reconcile pass in that window) rather than
crashing. Nothing re-ran `install-services.sh`, so the decision path stayed dead
for four hours and stayed silent: the adapter's old `/health` reported `ok:true`
even with a dead upstream, and the provider had `isReady: () => true`, so no log
line anywhere said the stack was down. The last recorded decision was Nov 20.

Diagnostics that were missing at the time and now exist: `kev-status.sh`,
`kev-watchdog.log`, upstream-aware `/health`, error records in `logs/usage.jsonl`,
and runner `/health` counters (`uptime_s`, `decisions`, `failures`, `last_error`).

## Deploying changes

```bash
scripts/install-services.sh        # re-renders plists, bootstraps all four units, waits for health
```
Plugin (`index.js`) changes need a gateway restart to take effect; service and
script changes do not.
