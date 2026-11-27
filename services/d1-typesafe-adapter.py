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
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("d1-typesafe-adapter")

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("KEV_ADAPTER_PORT", "8009"))
UPSTREAM = os.environ.get("KEV_UPSTREAM", "http://127.0.0.1:8090/v1/decisions")
# The runner serves /health at the origin root, not under the decisions path.
_default_origin = urllib.parse.urlsplit(UPSTREAM)
UPSTREAM_HEALTH = os.environ.get(
    "KEV_UPSTREAM_HEALTH",
    f"{_default_origin.scheme}://{_default_origin.netloc}/health",
)
MAX_BYTES = 4 * 1024 * 1024
# Keep strictly below the caller's own timeout so the adapter answers before the
# caller gives up (orphaned upstream work + broken pipes were the old failure).
UPSTREAM_TIMEOUT = float(os.environ.get("KEV_UPSTREAM_TIMEOUT", "12"))

_health_cache = {"ts": 0.0, "ok": False, "detail": "never"}


def upstream_health():
    """Cached liveness probe of the decision runner, so /health reports the
    truth about the whole chain instead of just this process."""
    now = time.monotonic()
    if now - _health_cache["ts"] < 5:
        return _health_cache
    _health_cache["ts"] = now
    try:
        with urllib.request.urlopen(UPSTREAM_HEALTH, timeout=1.5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        _health_cache["ok"] = True
        _health_cache["detail"] = {"model": body.get("model"), "loaded": body.get("loaded")}
    except Exception as exc:  # noqa: BLE001
        _health_cache["ok"] = False
        _health_cache["detail"] = str(exc)
    return _health_cache


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
    # The OpenClaw gateway contract v2 spells booleans "boolean"; the d1
    # engine only knows "noul". Older plugin builds pre-translated this,
    # newer ones pass "boolean" straight through, so normalize here too.
    if kind == "boolean":
        q["type"] = kind = "noul"
    if kind in ("noul", "bool"):
        # The engine requires the question itself as `instructions`. The
        # plugin sends it as `question`/`prompt`/`text` depending on build;
        # a missing instructions was the silent 400->502 root cause on
        # 2026-11-27 (gateway rejections with no adapter-visible detail).
        if not q.get("instructions"):
            text = None
            for k in ("question", "prompt", "text", "label", "description"):
                v = q.get(k)
                if isinstance(v, str) and v.strip():
                    text = v.strip()
                    break
            q["instructions"] = text or "Is this true given the situation?"
        if isinstance(criteria, list) and len(criteria) == 2:
            q["criteria"] = {
                "false": _entry_desc(criteria[0], _entry_label(criteria[0], 0)),
                "true": _entry_desc(criteria[1], _entry_label(criteria[1], 1)),
            }
        elif not isinstance(criteria, dict):
            q["criteria"] = {"false": "false", "true": "true"}
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
    # choice/score: ensure instructions too (engine requires them).
    if kind in ("choice", "score") and not q.get("instructions"):
        for k in ("question", "prompt", "text", "label"):
            v = q.get(k)
            if isinstance(v, str) and v.strip():
                q["instructions"] = v.strip()
                break
        else:
            q["instructions"] = "Choose the best option given the situation."
    # Strip plugin-side text fields the engine would choke on.
    for k in ("question", "prompt", "text", "label"):
        if "instructions" in q:
            q.pop(k, None)
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
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Caller hung up (timeout/abort). Expected; do not spam tracebacks.
            log.info("client disconnected before reading %s response", code)


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
            attempt = 0
            while True:
                attempt += 1
                try:
                    upstream = urllib.request.Request(
                        UPSTREAM,
                        data=upstream_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(upstream, timeout=UPSTREAM_TIMEOUT) as resp:
                        result = json.loads(resp.read().decode("utf-8"))
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    # One quick retry covers a runner that is mid-restart under
                    # launchd (kickstart/respawn) rather than actually down.
                    if attempt >= 2:
                        raise
                    log.warning("upstream attempt 1 failed (%s); retrying", exc)
                    time.sleep(1.0)
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
            # Failures must be visible in the service log: silent 502s were why
            # a three-day outage never showed up in any log line.
            log.error("adapter failure: %s: %s", type(exc).__name__, exc)
            self._send(502, {"error": f"adapter failure: {exc}"})

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            probe = upstream_health()
            self._send(
                200 if probe["ok"] else 503,
                {
                    "ok": bool(probe["ok"]),
                    "upstream": UPSTREAM,
                    "upstream_ok": bool(probe["ok"]),
                    "upstream_detail": probe["detail"],
                },
            )
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"typesafe adapter on http://{LISTEN_HOST}:{LISTEN_PORT}/v1/systemone -> {UPSTREAM}", flush=True)
    server.serve_forever()
