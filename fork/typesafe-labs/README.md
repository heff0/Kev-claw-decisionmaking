# Vendored `@openclaw/typesafe` plugin (released build)

Absorbed 2026-11-27 so we can extend the decision path in-repo instead of
waiting on upstream. This is the **released npm/dist build**, not upstream
TypeScript source — OpenClaw ships the plugin as compiled `dist/index.js`
(`bundledDist: false`, runtime entry `./dist/index.js`).

## Provenance

| Field | Value |
| --- | --- |
| Package | `@openclaw/typesafe` v2026.11.4 |
| Host that produced this copy | OpenClaw 2026.11.8 (2d4ab29), installed at `~/.openclaw/extensions/typesafe` |
| dist sha256 at absorb | see `DIST_SHA256` (unmodified baseline) |
| Compat | pluginApi `>=2026.11.4`, peer `openclaw>=2026.11.4`, minHost `>=2026.9.6` |

**Current state: unmodified.** The vendored copy is a baseline mirror; our
runtime still loads the plugin from `~/.openclaw/extensions/typesafe`.

## Why vendored

- Upstream only declares `boolean | choice | score` questionTypes in
  `openclaw.plugin.json`; the d1 engine also answers `text, number, date,
  box, boxes`. Extending those lanes requires either patching the plugin's
  capabilities/codec or doing everything in our bridge — vendoring gives us
  the option of shipping our own fork (e.g. provider id `typesafe/kev-labs`).
- Gateway↔plugin wire contract lives in `dist/index.js` (`/v1/systemone`
  codec, boolean→noul normalize, answer decode). Keeping a copy in-repo means
  `scripts/contract-test.py` can be diffed against a known baseline after
  every OpenClaw update instead of re-reverse-engineering live behaviour.

## How to re-absorb a new release

```sh
scripts/sync-typesafe.sh            # copy from installed extensions dir, show diff, update DIST_SHA256
scripts/sync-typesafe.sh --check    # compare installed vs vendor, no writes
```

After any re-absorb: run `scripts/contract-test.py` against the live bridge,
then decide whether patches to our vendored copy need re-applying.

## Install-a-fork path (when we start patching)

1. Edit `vendor/typesafe/openclaw.plugin.json` (provider id / capabilities)
   and `dist/index.js` as needed.
2. Point OpenClaw at the fork via plugin config or install from a local path;
   keep the stock plugin disabled to avoid two providers answering.
3. `decisionModel` switch stays hot-configurable; `typesafe/kev-latest`
   remains the fallback lane.
