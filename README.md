# openclaw-d1-local

Local typed decision-making for OpenClaw using **LiquidAI d1-Omni** (MLX, Apple
Silicon). One repo, three parts:

| Part | Path | Role |
| --- | --- | --- |
| OpenClaw plugin `d1-local` | `index.js`, `openclaw.plugin.json` | Registers a native decision provider (`d1-local/d1-omni-2b`) that talks to the runner over loopback. No hosted API, no adapter needed. |
| System One runner | `services/d1-systemone-server.py` | Preloads `LiquidAI/d1-Omni-2B-MLX-8bit` via mlx-vlm and serves `POST /v1/decisions` using the `model.system_one(...)` engine. Port **8090**. |
| TypeSafe bridge (optional) | `services/d1-typesafe-adapter.py` | Lets the upstream `typesafe` plugin (`kev-latest`) drive the same runner. Port **8009**. Superseded by the plugin; kept for compatibility experiments. |

## Why a custom runner?

mlx-vlm 0.7.x's stock HTTP server only implements the legacy decision dialect
(`model.predict` + `decision_types`). The d1-Omni 2B builds (`model_type
lfm3_omni`) expose the newer `model.system_one(state, questions, media)` engine,
which only the `mlx_vlm.decide` CLI dispatches to. This runner speaks that
engine over the same `/v1/decisions` contract.

## Wire it up

1. Prereqs: workspace `mlx-env` venv (mlx-vlm installed) and the 8-bit 2B model
   cached (`hf download LiquidAI/d1-Omni-2B-MLX-8bit`).
2. Services: `scripts/install-services.sh` (renders + bootstraps the launchd
   units; runner preload takes ~1 min, resident RAM ~3 GB).
3. Plugin: point OpenClaw at this folder and enable it in config:

```json5
{
  plugins: {
    allow: [..., "d1-local"],
    load: { paths: ["/Users/Shared/dev/Kev-claw-decisionmaking"] },
    entries: { "d1-local": { enabled: true, config: { baseUrl: "http://127.0.0.1:8090/v1/decisions" } } },
  },
  agents: { defaults: { decisionModel: "d1-local/d1-omni-2b" } },
}
```

After editing plugin code: `openclaw plugins reload d1-local` (config edits
hot-reload).

## Operations

- Health: `curl 127.0.0.1:8090/health` (and `127.0.0.1:8009/health` for the bridge).
- Logs: `workspace/logs/d1-server.{err.,}log`, `typesafe-adapter.{err.,}log`.
- Units: `com.openclaw.d1-server`, `com.openclaw.typesafe-adapter` (user LaunchAgents).
- Smoke test: `scripts/smoke.sh` posts choice/score/boolean in the wire dialect.
- Rollback to 600M via the typesafe plugin: restore the old plist ProgramArguments
  (`python -m mlx_vlm.server --decision-model LiquidAI/d1-omni-600M-MLX-8bit`)
  and set `decisionModel: "typesafe/kev-latest"` with the adapter baseUrl.

## Provenance

Built 2026-11-01 on the M4 Mac mini gateway; 2B warm latency ~200 ms/batch;
end-to-end verified through `decision_evaluate` and (bridge path) port 8009.
