# openclaw-d1-local

Local typed decision-making for OpenClaw using **LiquidAI d1-Omni** (MLX, Apple
Silicon). One repo, three parts:

> **Requirements:** Apple Silicon Mac (MLX + launchd only — no Linux/Intel path
> for the local runner; on other platforms use the upstream hosted
> `typesafe`/Jev route instead). OpenClaw **≥ 2026.10.9** (the recommended
> `typesafe/kev-latest` path needs the Decision V2 runtime).

| Part | Path | Role |
| --- | --- | --- |
| System One runner | `services/d1-systemone-server.py` | Preloads `LiquidAI/d1-Omni-2B-MLX-8bit` via mlx-vlm and serves `POST /v1/decisions` using the `model.system_one(...)` engine. Port **8090**. Logs per-decision usage to `logs/usage.jsonl`. |
| TypeSafe bridge | `services/d1-typesafe-adapter.py` | Serves the upstream `typesafe` plugin's `/v1/systemone` local-Kev dialect on port **8009** and translates to the runner. **Recommended path** (`decisionModel: typesafe/kev-latest`) — see [docs/typesafe-integration.md](docs/typesafe-integration.md). |
| OpenClaw plugin `d1-local` | `index.js`, `openclaw.plugin.json` | Registers a native decision provider (`d1-local/d1-omni-2b`) that talks to the runner over loopback directly. Fallback when the upstream `typesafe` plugin is unavailable. |

The stack **wraps around** the upstream [`typesafe` extension](https://github.com/openclaw/openclaw/tree/a5674cb817af92312dca57bc5eba9f467ee0347e/extensions/typesafe) (`@openclaw/typesafe`, tested at v2026.10.9 / commit `a5674cb8`) as a **soft dependency**: we implement its documented local-server contract without vendoring or forking it. Integration notes, upstream patch impact, and a post-update verification checklist live in [docs/typesafe-integration.md](docs/typesafe-integration.md).

## Why a custom runner?

mlx-vlm 0.7.x's stock HTTP server only implements the legacy decision dialect
(`model.predict` + `decision_types`). The d1-Omni 2B builds (`model_type
lfm3_omni`) expose the newer `model.system_one(state, questions, media)` engine,
which only the `mlx_vlm.decide` CLI dispatches to. This runner speaks that
engine over the same `/v1/decisions` contract.

## Wire it up

One command on a fresh Apple Silicon Mac (venv from pinned
`services/requirements.txt` → checkpoint download → renders + bootstraps the
launchd units → health check):

```sh
scripts/setup.sh              # overrides: KEV_VENV=... KEV_MODEL=...
```

Step by step, that is:

1. Prereqs: a venv with `services/requirements.txt` (mlx-vlm pinned to the
   `model.system_one` commit) and the model cached
   (`hf download LiquidAI/d1-Omni-2B-MLX-8bit`).
2. Services: `scripts/install-services.sh` renders
   `launchd/templates/*.plist.template` with this machine's paths and bootstraps
   the units (runner preload ~1 min, resident RAM ~3 GB).
3. Plugin: point OpenClaw at this folder and enable it in config:

```json5
{
  plugins: {
    allow: [..., "typesafe", "d1-local"],
    load: { paths: ["/Users/Shared/dev/Kev-claw-decisionmaking"] },
    entries: {
      typesafe: { enabled: true, config: { baseUrl: "http://127.0.0.1:8009" } },  // recommended
      "d1-local": { enabled: true, config: { baseUrl: "http://127.0.0.1:8090/v1/decisions" } }, // fallback
    },
  },
  agents: { defaults: { decisionModel: "typesafe/kev-latest" } },
}
```

After editing plugin code: `openclaw plugins reload d1-local` (config edits
hot-reload).

## Switching models (no agent needed)

`config/model.env` is the single source of truth for which checkpoint the
runner loads. Change it with one command:

```sh
scripts/set-model.sh LiquidAI/d1-Omni-600M-MLX-8bit   # validates on HF, downloads, swaps, restarts, health-checks
scripts/set-model.sh                                   # show current + cached models
scripts/set-model.sh --rollback                        # revert to previous checkpoint
```

The `decisionModel` selection in `openclaw.json` is a label only — it does not
load models, so only the runner needs restarting (auto-handled).

## Operations

- Health: `curl 127.0.0.1:8090/health` (and `127.0.0.1:8009/health` for the bridge).
- Logs: `workspace/logs/d1-server.{err.,}log`, `typesafe-adapter.{err.,}log`.
- Units: `com.openclaw.d1-server`, `com.openclaw.typesafe-adapter` (user LaunchAgents);
  the runner starts via `scripts/run-server.sh`, which sources `config/model.env`
  (`KEV_MODEL`, `KEV_PYTHON`).
- Log hygiene: `scripts/clean-logs.sh` + LaunchAgent `com.openclaw.kev-log-clean`
  prune `usage.jsonl` records older than 90 days and truncate oversized launchd
  logs daily at 03:15.
- Usage & cost: `scripts/kev-usage-report.py` reports decision counts, token
  totals, equivalent hosted-API cost (TypeSafe Jev reference rates), and
  hardware payback (`--hardware-cost`, `--monthly`).
- Smoke test: `scripts/smoke.sh` posts choice/score/boolean in the wire dialect.
- Rollback to 600M via the typesafe plugin: restore the old plist ProgramArguments
  (`python -m mlx_vlm.server --decision-model LiquidAI/d1-omni-600M-MLX-8bit`)
  and set `decisionModel: "typesafe/kev-latest"` with the adapter baseUrl.

## Provenance

Built 2026-11-01 on the M4 Mac mini gateway; 2B warm latency ~200 ms/batch;
end-to-end verified through `decision_evaluate` on both routes: `typesafe/kev-latest`
(bridge path, port 8009, usage accounting live) and `d1-local/d1-omni-2b` (native plugin).
