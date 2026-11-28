`services/d1-typesafe-adapter.py` (:8009) proxies to the System One runner (:8090, `services/d1-systemone-server.py`). When the runner rejects a batch with 400 + a precise message (e.g. "score criteria must list 2-10 levels lowest first", "unknown question type 'text'", "instructions must be the question text"), the adapter returns 502 with generic text; the engine's detail never reaches the caller or the gateway log.

Impact: upstream issue 2's generic errors get *another* lossy layer; debugging requires tailing `logs/d1-server.err.log` by hand.

Fix: pass through the upstream 400 body verbatim as a structured error (`{"error": {"layer": "engine", "detail": <runner message>}}`), and only use 502 for genuine transport failures. Add a contract-test case asserting the 400 detail survives the round trip.

Evidence: 2026-11-27 test session — runner 400s for score-with-1-level and type=text; adapter surfaced 502 with no detail (ledger: "Engine 400s surfaced as adapter 502").