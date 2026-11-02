#!/usr/bin/env python3
"""Full contract test for the Kev decision API: every method on both services.

Covers:
  runner  127.0.0.1:8090  GET  /health, POST /v1/decisions
          question types: noul, choice, score, text, number, date, box/boxes
  adapter 127.0.0.1:8009  GET  /health, POST /v1/systemone (plugin wire dialect)
          list-criteria translation + strict plugin answer shapes

Asserts observed-on-this-build behavior (captured 2026-11-01):
  - engine accepts noul/choice/score/text/number/date/box/boxes; rejects 'bool'
  - text/number/date return value=None with a 'found' probability when the
    state has no extractable span (contract: keys present, no crash)
  - box/boxes with no image -> 400 with explanatory error
  - adapter strips value/probability/metadata; emits noul/choice/score shapes

Exit 0 iff every check passes. Usage: contract-test.py [--quick]
(--quick skips the heavyweight multi-question/large-payload checks.)
"""
import json
import sys
import time
import urllib.error
import urllib.request

RUNNER = "http://127.0.0.1:8090"
ADAPTER = "http://127.0.0.1:8009"
TIMEOUT = 120

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL':4s}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def http(url, method="GET", body=None, timeout=TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}
    except Exception as e:  # noqa: BLE016
        return None, {"error": str(e)}


STATE = {"message": "Checkout is failing for every customer since the last deploy."}
NOUL_Q = {"type": "noul", "instructions": "Does this need incident response?",
          "criteria": {"false": "A routine request can follow normal handling",
                       "true": "Widespread service outages"}}
CHOICE_Q = {"type": "choice", "instructions": "Route this request.",
            "criteria": {"support": "Service failures", "billing": "Payment questions"}}
SCORE_Q = {"type": "score", "instructions": "How urgent?",
           "criteria": ["No disruption", "One workflow blocked", "Widespread outage"]}


def main():
    quick = "--quick" in sys.argv
    t0 = time.time()

    print("runner GET /health")
    code, h = http(RUNNER + "/health")
    check("runner /health 200 ok=true", code == 200 and h.get("ok") is True, str(h)[:120])
    check("runner /health model+loaded keys", "model" in h and "loaded" in h, str(h)[:120])

    print("runner GET unknown path")
    code, _ = http(RUNNER + "/nope")
    check("runner unknown GET -> 404", code == 404, f"got {code}")

    print("runner POST /v1/decisions question types")
    for name, q, expect_keys, min_ms in [
        ("noul", NOUL_Q, None, None),
        ("choice", CHOICE_Q, ("type", "choice", "confidence", "probabilities"), None),
        ("score", SCORE_Q, ("type", "score", "confidence", "probabilities"), None),
        ("text", {"type": "text", "instructions": "Who is affected?"}, ("type", "found"), None),
        ("number", {"type": "number", "instructions": "How many customers?"}, ("type", "found"), None),
        ("date", {"type": "date", "instructions": "When did it start?"}, ("type", "found"), None),
    ]:
        code, r = http(RUNNER + "/v1/decisions", "POST",
                       {"state": STATE, "questions": {"probe": q}})
        ans = (r.get("answers") or {}).get("probe") or {}
        ok = code == 200 and ans.get("type") == name and all(k in ans for k in (expect_keys or ()))
        if name == "noul":
            # d1 engine returns {type:noul,noul:prob} on the raw runner dialect;
            # legacy mlx-vlm dialect uses {type:bool,value,probability}. Accept either.
            ok = ok and any(k in ans and 0 <= ans[k] <= 1 for k in ("noul", "probability"))
        check(f"runner question type={name}", ok, f"code={code} ans={json.dumps(ans)[:140]}")

    print("runner box/boxes image contract")
    for bt in ("box", "boxes"):
        code, r = http(RUNNER + "/v1/decisions", "POST", {
            "state": STATE,
            "questions": {"probe": {"type": bt, "instructions": "Where is the logo?",
                                    "criteria": {"logo": "the logo"}}}})
        check(f"runner {bt} without image -> 400 explanatory",
              code == 400 and "media" in json.dumps(r), f"code={code} {json.dumps(r)[:120]}")

    print("runner invalid inputs")
    code, _ = http(RUNNER + "/v1/decisions", "POST", {"state": STATE, "questions": {}})
    check("runner empty questions -> 400", code == 400, f"got {code}")
    code, r = http(RUNNER + "/v1/decisions", "POST", {
        "state": STATE, "questions": {"x": {"type": "bool", "instructions": "?",
                                            "criteria": {"false": "no", "true": "yes"}}}})
    check("runner type=bool -> 400 (engine rejects legacy alias)",
          code == 400 and "not 'bool'" in json.dumps(r), f"code={code}")
    code, _ = http(RUNNER + "/v1/unknown", "POST", {"questions": {"x": CHOICE_Q}})
    check("runner unknown POST path -> 404", code == 404, f"got {code}")

    if not quick:
        print("runner multi-question + usage")
        code, r = http(RUNNER + "/v1/decisions", "POST", {
            "state": STATE,
            "questions": {"route": CHOICE_Q, "urgency": SCORE_Q, "escalate": NOUL_Q}})
        ans = r.get("answers") or {}
        ok = (code == 200 and len(ans) == 3
              and ans.get("route", {}).get("choice") == "support"
              and 0.0 <= ans.get("escalate", {}).get("noul", ans.get("escalate", {}).get("probability", -1)) <= 1.0
              and "usage" in r and "model" in r)
        check("runner 3-question batch + usage/model echo", ok, json.dumps(r)[:180])

    print("adapter GET /health")
    code, h = http(ADAPTER + "/health")
    check("adapter /health 200 ok=true upstream set",
          code == 200 and h.get("ok") is True and "upstream" in h, str(h)[:120])

    print("adapter POST /v1/systemone wire dialect")
    code, r = http(ADAPTER + "/v1/systemone", "POST", {
        "model": "typesafe-kev",
        "state": STATE,
        "questions": {"route": CHOICE_Q, "urgency": SCORE_Q, "escalate": NOUL_Q},
    })
    a = (r.get("answers") or {})
    esc, rt, ur = a.get("escalate", {}), a.get("route", {}), a.get("urgency", {})
    check("adapter noul -> {type:noul,noul}", esc.get("type") == "noul" and 0 <= esc.get("noul", -1) <= 1,
          json.dumps(esc)[:120])
    check("adapter choice -> {choice,confidence,probabilities}",
          rt.get("type") == "choice" and isinstance(rt.get("choice"), str)
          and "confidence" in rt and isinstance(rt.get("probabilities"), dict),
          json.dumps(rt)[:120])
    check("adapter score -> {score,confidence,probabilities,legend}",
          ur.get("type") == "score" and "score" in ur and "legend" in ur
          and isinstance(ur.get("probabilities"), dict), json.dumps(ur)[:120])
    check("adapter strict-shape: no value/probability/metadata leakage",
          all(not ({"value", "probability", "metadata"} & set(x)) for x in (esc, rt, ur)),
          json.dumps([esc, rt, ur])[:200])
    check("adapter echoes requested model label", r.get("model") == "typesafe-kev", str(r.get("model")))
    check("adapter adds latency_ms", isinstance(r.get("latency_ms"), int), str(r)[:120])

    print("adapter list-criteria translation (plugin sends lists)")
    code, r = http(ADAPTER + "/v1/systemone", "POST", {
        "model": "wire-probe",
        "state": {"message": "Invoice shows the wrong amount, twice now."},
        "questions": {
            "c": {"type": "choice", "instructions": "Route.", "criteria": ["support", "billing"]},
            "b": {"type": "noul", "instructions": "Billing issue?",
                  "criteria": ["routine request", "billing dispute"]},
        }})
    ca = (r.get("answers") or {}).get("c", {})
    ba = (r.get("answers") or {}).get("b", {})
    check("adapter choice list-criteria -> choice answer with mapped labels",
          code == 200 and ca.get("type") == "choice" and ca.get("choice") in ("support", "billing"),
          json.dumps(ca)[:140])
    check("adapter noul list-criteria -> noul prob",
          ba.get("type") == "noul" and 0 <= ba.get("noul", -1) <= 1, json.dumps(ba)[:140])

    print("adapter invalid inputs + upstream error passthrough")
    code, _ = http(ADAPTER + "/nope", "POST", {"questions": {"x": CHOICE_Q}})
    check("adapter unknown POST path -> 404", code == 404, f"got {code}")
    code, r = http(ADAPTER + "/v1/systemone", "POST", {"state": STATE, "questions": {}})
    check("adapter empty questions -> 4xx/5xx error json",
          code in (400, 502) and "error" in r, f"code={code} {str(r)[:100]}")

    print()
    failed = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed in {time.time() - t0:.1f}s")
    if failed:
        print("FAILED:", *failed, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    main()
