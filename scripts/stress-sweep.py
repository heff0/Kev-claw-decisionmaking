#!/usr/bin/env python3
"""Decision-lane stress sweep (dry run, read-only).

Feeds EVERY row of Task-Tracker.xlsx through the typesafe bridge
(127.0.0.1:8009/v1/systemone) using the exact gateway contract-v2 wire
dialect (boolean + question, list criteria), in batches, then hammers a
suite of adversarial inputs designed to make the lane fail. Records
per-batch HTTP status, latency, missing/invalid answers, and answer
distributions. Writes a JSON report; touches no tracker cells.
"""
import json
import time
import urllib.error
import urllib.request

import openpyxl

ADAPTER = "http://127.0.0.1:8009/v1/systemone"
TRACKER = "/Users/remote-mac/Documents/Sheets/trackers/Task-Tracker.xlsx"
OUT = "/Users/remote-mac/.openclaw/workspace/logs/decision-stress-2026-11-27-v2.json"
PROBS = "/Users/remote-mac/.openclaw/workspace/logs/decision-stress-probs-2026-11-27.json"
BATCH = 40

report = {"batches": [], "adversarial": [], "summary": {}}
all_probs = {}


def post(payload, timeout=900):
    req = urllib.request.Request(
        ADAPTER,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return 200, body, int((time.perf_counter() - started) * 1000)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {}
        return exc.code, body, int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}, int((time.perf_counter() - started) * 1000)


def check_answers(questions, body):
    """Return list of per-question failures for an expected 200."""
    fails = []
    answers = body.get("answers") or {}
    for qid in questions:
        a = answers.get(qid)
        if a is None:
            fails.append(f"{qid}: missing answer")
            continue
        t = a.get("type")
        if t == "boolean":
            p = a.get("probabilityTrue", a.get("probability"))
        elif t == "noul":
            p = a.get("noul")
        else:
            fails.append(f"{qid}: wrong type {t}")
            continue
        if not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
            fails.append(f"{qid}: bad probability {p!r}")
    return fails


def sweep_tracker():
    wb = openpyxl.load_workbook(TRACKER, read_only=True, data_only=True)
    ws = wb["Tasks"]
    rows = list(ws.iter_rows(values_only=True))
    tasks = [
        {"id": r[0], "project": r[1] or "", "task": str(r[3] or ""), "status": r[4] or "", "priority": r[5] or ""}
        for r in rows[1:]
        if r and r[0] is not None
    ]
    report["summary"]["tasks_total"] = len(tasks)
    total_ok = total_q = 0
    for i in range(0, len(tasks), BATCH):
        chunk = tasks[i : i + BATCH]
        questions = {}
        for t in chunk:
            questions[f"t{t['id']}"] = {
                "type": "boolean",
                "question": f"Act on this task within 7 days? [{t['project']}/{t['priority']}] {t['task'][:140]}",
                "criteria": ["defer", "act"],
            }
        state = {"domain": "heff task tracker", "batch": i // BATCH, "items": [f"{t['id']}:{t['task'][:60]}" for t in chunk]}
        code, body, lat = post({"contractVersion": 2, "questions": questions, "state": state})
        fails = check_answers(questions, body) if code == 200 else []
        probs = []
        for a in (body.get("answers") or {}).values():
            v = a.get("probabilityTrue", a.get("probability", a.get("noul")))
            if isinstance(v, (int, float)):
                probs.append(v)
        entry = {
            "batch": i // BATCH,
            "questions": len(questions),
            "http": code,
            "latency_ms": lat,
            "answer_failures": fails,
            "prob_min": min(probs) if probs else None,
            "prob_max": max(probs) if probs else None,
            "error": body.get("error") if code != 200 else None,
        }
        for t, a in zip(chunk, (body.get("answers") or {}).values()):
            v = a.get("probabilityTrue", a.get("probability", a.get("noul"))) if isinstance(a, dict) else None
            all_probs[str(t["id"])] = {"task": t["task"][:70], "project": t["project"], "priority": t["priority"], "p": v}
        report["batches"].append(entry)
        total_q += len(questions)
        if code == 200 and not fails:
            total_ok += len(questions)
        print(f"batch {i//BATCH}: {len(questions)}q http={code} {lat}ms fails={len(fails)}", flush=True)
    report["summary"]["questions"] = total_q
    report["summary"]["fully_ok_questions"] = total_ok


def adversarial():
    cases = {
        "single_criterion_choice": {"q": {"type": "choice", "question": "Pick one", "criteria": {"only": "the only option"}}},
        "score_one_level": {"q": {"type": "score", "question": "Rate", "criteria": ["only"]}},
        "long_instructions": {"q": {"type": "boolean", "question": "x" * 6000, "criteria": ["no", "yes"]}},
        "unicode_quotes": {"q": {"type": "boolean", "question": 'Fix "torque spec" — 3/8" drive ✅ — 100% à l\'atelier?\nnewline', "criteria": ["no", "yes"]}},
        "empty_question_text": {"q": {"type": "boolean", "question": "", "criteria": ["no", "yes"]}},
        "missing_criteria": {"q": {"type": "boolean", "question": "no criteria here"}},
        "unknown_type": {"q": {"type": "text", "question": "Summarize the blocker", "criteria": []}},
        "null_state_none": None,
        "huge_batch_120": "generate",
        "boolean_64_keys": "generate_bool64",
    }
    state = {"domain": "adversarial"}
    for name, spec in cases.items():
        if name == "null_state_none":
            payload = {"contractVersion": 2, "questions": {"q": {"type": "boolean", "question": "still ok?", "criteria": ["no", "yes"]}}, "state": None}
        elif spec == "generate":
            payload = {"contractVersion": 2, "state": state, "questions": {f"q{n}": {"type": "boolean", "question": f"Task {n}: buy {n} bolts before Friday deadline review", "criteria": ["no", "yes"]} for n in range(120)}}
        elif spec == "generate_bool64":
            payload = {"contractVersion": 2, "state": state, "questions": {f"b{n}": {"type": "boolean", "question": f"boolean {n}", "criteria": ["no", "yes"]} for n in range(64)}}
        else:
            payload = {"contractVersion": 2, "state": state, "questions": {"q": dict(spec["q"])}}
        code, body, lat = post(payload)
        report["adversarial"].append({"case": name, "http": code, "latency_ms": lat, "answers": len(body.get("answers") or {}), "error": (body.get("error") or "")[:200]})
        print(f"adv {name}: http={code} {lat}ms answers={len(body.get('answers') or {})} err={(body.get('error') or '')[:80]}", flush=True)


if __name__ == "__main__":
    sweep_tracker()
    adversarial()
    b = report["batches"]
    ok = sum(1 for e in b if e["http"] == 200 and not e["answer_failures"])
    report["summary"].update(
        batches=len(b),
        batches_ok=ok,
        batches_failed_http=sum(1 for e in b if e["http"] != 200),
        batches_with_answer_failures=sum(1 for e in b if e["http"] == 200 and e["answer_failures"]),
        total_answer_failures=sum(len(e["answer_failures"]) for e in b),
        latency_max_ms=max((e["latency_ms"] for e in b), default=0),
        latency_total_ms=sum(e["latency_ms"] for e in b),
    )
    with open(PROBS, "w") as fh:
        json.dump(all_probs, fh, indent=2)
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print("SUMMARY", json.dumps(report["summary"]))
