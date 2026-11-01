#!/usr/bin/env python3
"""Graded eval for the local d1-Omni decision runner.

Posts each case in eval/cases.json to the runner's /v1/decisions endpoint
and checks expectations (answer label/score, min confidence, noul bounds).
Exit 0 if all pass, 1 otherwise. Usage: grade.py [base_url] [cases.json]
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090/v1/decisions"
CASES = sys.argv[2] if len(sys.argv) > 2 else None

import os
cases_path = CASES or os.path.join(os.path.dirname(__file__), "..", "eval", "cases.json")
cases = json.load(open(cases_path))["cases"]

passed = 0
failed = []
latencies = []

for case in cases:
    body = json.dumps({"state": case["state"], "questions": case["questions"]}).encode()
    req = urllib.request.Request(BASE, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.load(resp)
    lat = int((time.time() - t0) * 1000)
    latencies.append(lat)

    answers = result["answers"]
    reasons = []
    for qid, exp in case["expect"].items():
        ans = answers.get(qid)
        if ans is None:
            reasons.append(f"{qid}: no answer")
            continue
        qtype = exp["type"]
        if qtype == "noul":
            v = ans.get("noul")
            if "min" in exp and v < exp["min"]:
                reasons.append(f"{qid}: noul {v:.3f} < min {exp['min']}")
            if "max" in exp and v > exp["max"]:
                reasons.append(f"{qid}: noul {v:.3f} > max {exp['max']}")
        elif qtype == "choice":
            if ans.get("choice") != exp["answer"]:
                reasons.append(f"{qid}: choice {ans.get('choice')!r} != {exp['answer']!r}")
            conf = ans.get("confidence", 0)
            if "min_confidence" in exp and conf < exp["min_confidence"]:
                reasons.append(f"{qid}: confidence {conf:.3f} < {exp['min_confidence']}")
        elif qtype == "score":
            if ans.get("score") != exp["answer"]:
                reasons.append(f"{qid}: score {ans.get('score')} != {exp['answer']}")

    status = "PASS" if not reasons else "FAIL"
    why = "; ".join(reasons) if reasons else f"{lat} ms"
    print(f"[{status}] {case['id']}: {why}")
    if not reasons:
        passed += 1
    else:
        failed.append(case["id"])

total = len(cases)
avg = sum(latencies) // len(latencies) if latencies else 0
print(f"\nScore: {passed}/{total}  (avg latency {avg} ms)")
if failed:
    print("Failed:", ", ".join(failed))
sys.exit(0 if passed == total else 1)
