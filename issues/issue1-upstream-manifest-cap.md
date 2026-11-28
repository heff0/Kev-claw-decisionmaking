OpenClaw 2026.11.8 (2d4ab29).

A local decision plugin + runner (LiquidAI d1-Omni-2B-MLX-8bit via mlx-vlm) supports question types `noul, choice, score, text, number, date, box, boxes`. The gateway rejects any plugin manifest declaring more than 3 questionTypes, and rejects anything outside `boolean/choice/score` — enforced in `dist/manifest-capability-normalizers` (observed at install). Consequences:

1. Plugin lane can never expose text/number/date/box/boxes even when the engine supports them.
2. Gateway Decision V2 core itself only understands `boolean/choice/tags/score/sort` — so lifting the manifest cap alone is insufficient; contract widening must be coordinated across gateway core, tool schema, plugin codec, and validator.

Repro: declare `questionTypes: [boolean, choice, score, noul]` in a decisionModel manifest → plugin load rejects with cap error.

Requested: a roadmap for widening the decision question-type contract, or an explicit documented "3 types only" contract so plugin authors can rely on it. Currently the cap is undocumented and the dist is sha256-inventory-protected, so there is no supported local workaround.

Workaround in use: normalize richer types down to boolean/choice/score in an adapter (`d1-typesafe-adapter.py`: boolean→noul, criteria list→false/true maps, label→description maps) with reverse-mapping on answers. Works, but caps capability and adds a bridge service.

---