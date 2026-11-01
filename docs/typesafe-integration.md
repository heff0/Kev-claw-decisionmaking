# TypeSafe extension integration (soft dependency)

**Upstream:** [`extensions/typesafe` in openclaw/openclaw](https://github.com/openclaw/openclaw/tree/a5674cb817af92312dca57bc5eba9f467ee0347e/extensions/typesafe)
**Plugin:** `@openclaw/typesafe` v2026.10.9 · verified against upstream commit `a5674cb8` (2026-11-01)

## What this repo is, relative to the TypeSafe plugin

The upstream `typesafe` extension is OpenClaw's official typed-decision provider
plugin. Its README documents two backends:

- **Hosted:** TypeSafe AI Jev (`typesafe/jev-latest`, needs a SecretRef credential)
- **Local:** "a local Kev System One server" served at `plugins.entries.typesafe.config.baseUrl`
  (e.g. `http://127.0.0.1:8009`), selected as `typesafe/kev-latest`, no credential.

**This repo implements the local-Kev server side of that contract.** The plugin
`POST /v1/systemone` call lands on our bridge (`services/d1-typesafe-adapter.py`,
port 8009), which translates it into the mlx-vlm `model.system_one` dialect and
forwards to our runner (`services/d1-systemone-server.py`, port 8090).

## Soft-dependency semantics

We wrap around `typesafe`; we do **not** vendor, fork, or patch it.

- No build or install coupling: the plugin comes from the OpenClaw release
  (`openclaw plugins install @openclaw/typesafe`). We only implement its
  documented HTTP dialect.
- If the plugin is absent or disabled, the stack still works end-to-end via the
  native `d1-local` plugin in this repo (`d1-local/d1-omni-2b` → port 8090
  directly). Both providers are registered and hot-switchable via
  `agents.defaults.decisionModel`.
- If upstream changes the wire format, the failure mode is isolated to
  `d1-typesafe-adapter.py` (translation layer) — the runner is unaffected.

### Current deployment (verified 2026-11-01)

```json5
{
  plugins: {
    entries: {
      typesafe: { enabled: true, config: { baseUrl: "http://127.0.0.1:8009" } },
      "d1-local": { enabled: true, config: { baseUrl: "http://127.0.0.1:8090/v1/decisions" } },
    },
  },
  agents: { defaults: { decisionModel: "typesafe/kev-latest" } }, // d1-local/d1-omni-2b = fallback
}
```

The `typesafe` path is **preferred** over `d1-local` because usage accounting
(input/output token counts) flows through the plugin into OpenClaw decision
results and our `logs/usage.jsonl`, enabling equivalent-API cost reporting
(`scripts/kev-usage-report.py`).

## What's been patched upstream (and impact on us)

Recent commits touching `extensions/typesafe` at the pinned commit:

| Upstream patch | Affects our wrap? |
| --- | --- |
| `44f536d` refactor(typesafe): evaluate Decisions directly with **Decision V2 contracts** | ⚠️ Requires the prepared V2 decision runtime. Upstream README notes released OpenClaw `2026.10.4` exposes V1 only. Our gateway (2026.10.9 line) pairs with this plugin and is verified live. |
| `c993c8f` refactor(typesafe): isolate Decision and local protocol codecs | The `/v1/systemone` local dialect now lives in `src/decision-codec.ts` — wire format stable, no adapter change needed. |
| `3d2d50b` fix(typesafe): keep OpenRouter requests on their own endpoint | Hosted path only. No impact. |
| `09b55d3` feat(typesafe): judge V2 image state with hosted Jev | Image state is **hosted-Jev only**. Our local runner accepts `images` but upstream never sends them on the local path yet. |
| `3ec9f3e` refactor(typesafe): decode decision results without V1 round trip | Response decoding only; our bridge response (model/answers/usage/latency_ms) unchanged and verified. |

### Patches on **our** side (things upstream doesn't do for you)

1. **Custom runner.** mlx-vlm 0.7.x's stock `mlx_vlm.server` only speaks the
   legacy `model.predict` + `decision_types` dialect; `d1-Omni-2B`
   (`model_type lfm3_omni`) needs `model.system_one(state, questions, media)`.
   Hence `d1-systemone-server.py`.
2. **Question-dialect translation** (`d1-typesafe-adapter.py`): TypeSafe sends
   choice criteria as ordered lists; the d1 engine wants label→description maps
   (a bare list is read as score levels). The adapter converts and maps answers
   back, including bool↔noul handling.
3. **Usage accounting:** the runner logs every decision to `logs/usage.jsonl`
   (tokens, latency, question count) and the bridge passes `usage` through so
   OpenClaw decision receipts carry token counts.

## Question wire format (as accepted by the runner)

Types are `noul, choice, score, text, number, date, box, boxes`.
`noul` requires `instructions` (the question text) and optionally `criteria`
(`{"false": ..., "true": ...}`). Verified example:

```sh
curl -s http://127.0.0.1:8090/v1/decisions -H 'Content-Type: application/json' -d '{
  "state": "kevin is deciding about dinner on a sunday evening",
  "questions": {
    "order_pizza": {
      "type": "noul",
      "instructions": "Should kevin order pizza tonight?",
      "criteria": {"false": "no strong reason to order", "true": "a strong reason to order"}
    }
  }
}'
```

## Re-verification checklist after an OpenClaw update

1. `curl -s 127.0.0.1:8009/health` → `{"ok": true, "upstream": "...8090/v1/decisions"}`
2. The noul curl above → 200 with an `answers` object and `usage` field.
3. Ask the agent to run `decision_evaluate` → expect `model: kev-latest`,
   `providerId: typesafe`, nonzero `usage.inputTokens`.
4. If (3) fails with a contract/version error: check `openclaw --version`
   pairing (Decision V2 runtime required, see README note above) and
   `openclaw plugins list` for the `typesafe` entry.
5. Fallback while investigating: set `decisionModel: "d1-local/d1-omni-2b"`.
