#!/usr/bin/env python3
"""TypeSafe System One wire adapter for the mlx-vlm d1-omni decision server.

Exposes POST /v1/systemone (loopback) on port 8009 and translates to/from the
mlx-vlm /v1/decisions dialect on 127.0.0.1:8090 so the OpenClaw `typesafe`
plugin can use d1-omni as a local decision provider via
plugins.entries.typesafe.config.baseUrl.

Dialect differences handled:
- noul/bool: plugin sends type "noul", expects {type:"noul", noul: prob};
  mlx-vlm answers with {type:"bool", value, probability}.
- choice: plugin expects {choice, confidence, probabilities};
  mlx-vlm returns {value, probabilities, metadata.confidence}.
- score: plugin expects {score, confidence, probabilities, legend};
  mlx-vlm returns {value, probabilities, metadata:{confidence, legend}}.
- Plugin is strict about extra fields: metadata/value/probability stripped.
- Response model echoes the plugin-requested model label (pattern-checked).
- latency_ms is optional and accepted by the plugin; we add it.
"""

import json
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8009
UPSTREAM = "http://127.0.0.1:8090/v1/decisions"
MAX_BYTES = 4 * 1024 * 1024


def _entry_label(entry, i):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        label = entry.get("label") or entry.get("name")
        if isinstance(label, str) and label:
            return label
    return str(entry)


def _entry_desc(entry, label):
    if isinstance(entry, dict):
        desc = entry.get("description") or entry.get("desc")
        if isinstance(desc, str) and desc:
            return desc
    return label


def translate_question(q):
    """Wire question -> d1 system_one question.

    The LFM3-based d1 engine wants choice criteria as label->description maps
    (a list is read as score levels), while the TypeSafe plugin sends ordered
    lists. Convert lists to maps and remember how to map labels back.
    """
    q = dict(q)
    kind = q.get("type")
    criteria = q.get("criteria")
    if kind == "choice" and isinstance(criteria, list):
        keymap = {}
        mapping = {}
        for i, entry in enumerate(criteria):
            label = _entry_label(entry, i)
            key = label if label not in mapping else f"{label}[{i}]"
            mapping[key] = _entry_desc(entry, label)
            keymap[key] = label
        q["criteria"] = mapping
        q["_keymap"] = keymap
    elif kind == "score" and isinstance(criteria, list):
        q["criteria"] = [_entry_desc(e, _entry_label(e, i)) for i, e in enumerate(criteria)]
    elif kind in ("noul", "bool") and isinstance(criteria, list) and len(criteria) == 2:
        q["criteria"] = {
            "false": _entry_desc(criteria[0], _entry_label(criteria[0], 0)),
            "true": _entry_desc(criteria[1], _entry_label(criteria[1], 1)),
        }
    return q


def translate_answer(ans, wire_q):
    t = ans.get("type")
    if t == "bool":
        p = ans.get("probability", 0.0)
        return {"type": "noul", "noul": p}
    if t == "noul":
        return {"type": "noul", "noul": ans.get("noul", 0.0)}
    if t == "choice":
        meta = ans.get("metadata") or {}
        keymap = wire_q.get("_keymap") or {}
        probs = ans.get("probabilities") or {}
        probs = {keymap.get(k, k): v for k, v in probs.items()}
        confidence = ans.get("confidence", meta.get("confidence"))
        if confidence is None and probs:
            confidence = max(probs.values())
        return {
            "type": "choice",
            "choice": keymap.get(ans.get("choice"), ans.get("choice", ans.get("value"))),
            "confidence": confidence,
            "probabilities": probs,
        }
    if t == "score":
        meta = ans.get("metadata") or {}
        probs = ans.get("probabilities") or {}
        confidence = ans.get("confidence", meta.get("confidence"))
        if confidence is None and probs:
            confidence = max(probs.values())
        criteria = wire_q.get("criteria") or []
        legend = (
            ans.get("legend")
            or meta.get("legend")
            or {str(i): c for i, c in enumerate(criteria)}
        )
        return {
            "type": "score",
            "score": ans.get("score", ans.get("value", 0.0)),
            "confidence": confidence,
            "probabilities": probs,
            "legend": legend,
        }
    # multi_label or unknown: pass through unchanged (plugin will reject if invalid)
    return ans


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/systemone":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BYTES:
                self._send(400, {"error": "bad content length"})
                return
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            questions = req.get("questions") or {}
            wire_questions = {k: translate_question(v) for k, v in questions.items()}
            payload = {"state": req.get("state"), "questions": wire_questions}
            upstream_body = json.dumps(payload).encode("utf-8")
            started = time.perf_counter()
            upstream = urllib.request.Request(
                UPSTREAM,
                data=upstream_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(upstream, timeout=55) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            latency = int((time.perf_counter() - started) * 1000)
            answers = {
                qid: translate_answer(ans, wire_questions.get(qid, {}))
                for qid, ans in (result.get("answers") or {}).items()
            }
            out = {
                "model": req.get("model") or result.get("model"),
                "answers": answers,
                "usage": result.get("usage", {"input_tokens": 0, "output_tokens": 0}),
                "latency_ms": latency,
            }
            self._send(200, out)
        except Exception as exc:  # noqa: BLE001
            self._send(502, {"error": f"adapter failure: {exc}"})

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "upstream": UPSTREAM})
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"typesafe adapter on http://{LISTEN_HOST}:{LISTEN_PORT}/v1/systemone -> {UPSTREAM}", flush=True)
    server.serve_forever()
