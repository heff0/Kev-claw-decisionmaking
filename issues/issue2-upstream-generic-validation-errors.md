OpenClaw 2026.11.8 (2d4ab29), tool `decision_evaluate` (contract v2).

Observed 2026-11-27 (9+ failed attempts logged in failure ledger; each defect produced an indistinguishable or misplaced error):

| Actual defect | Error returned |
|---|---|
| `state` omitted | "state: must have required property 'state'" (ok, precise) |
| `state` present but untagged (`{...}` instead of `{"type":"json","value":{...}}`) | "provide state and a nonempty questions map" — **misleading; state was present, tagging was the problem** |
| Extra top-level fields | generic map error, doesn't name the offending key |
| Malformed nested criteria shape | generic map error, doesn't name the question or field |

The untagged-state case is the worst: the message implies state is missing when it is present, sending callers in circles (this cost 3 retries in one session; source of the message appears to be dist/validation-v2 `validateDecisionBatchV2`, which validates the tag union after the presence check but reports the presence error).

Requested: per-defect error text naming (a) the failing layer (tool schema vs batch validator vs question validator), (b) the field path, (c) the expected shape. E.g. `state.type: untagged object; expected {"type":"json","value":…} or {"type":"text","text":…}`.

Also: the compact tool schema shown to models does not make `state` visibly required or show the tag union — worth surfacing in the tool description.

---