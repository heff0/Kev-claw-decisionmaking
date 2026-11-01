#!/usr/bin/env python3
"""System One decision server for LiquidAI d1-Omni (LFM3-based builds).

The stock `mlx_vlm.server` /v1/decisions path only supports the legacy
decision dialect (`model.predict` + `decision_types`). The 2B d1-Omni-2B
builds (model_type lfm3_omni, architectures D1Model) expose the newer
`model.system_one(state, questions, media)` engine instead, which mlx-vlm's
CLI (mlx_vlm.decide) dispatches to but its HTTP server does not. This runner
speaks the same /v1/decisions contract the d1-typesafe-adapter expects.

Wire contract (same shape the adapter previously got from mlx-vlm):
  POST /v1/decisions {"state": str|dict|null, "questions": {name: {...}},
                      "images": [paths]?}
  -> 200 {"answers": {name: {...}}, "usage": {...}, "model": MODEL}
  GET  /health -> {"ok": true, "model": MODEL, "loaded": bool}
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "LiquidAI/d1-Omni-2B-MLX-8bit"
USAGE_LOG = Path(__file__).resolve().parent.parent / "logs" / "usage.jsonl"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8090
MAX_BYTES = 64 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("d1-systemone")

_lock = threading.Lock()
_model = None
_processor = None


def ensure_model():
    global _model, _processor
    if _model is not None:
        return _model
    from mlx_vlm.utils import load

    started = time.perf_counter()
    _model, _processor = load(MODEL)
    log.info("loaded %s in %.1fs", MODEL, time.perf_counter() - started)
    return _model


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/decisions":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BYTES:
                self._send(400, {"error": "bad content length"})
                return
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            questions = req.get("questions")
            if not isinstance(questions, dict) or not questions:
                self._send(400, {"error": "questions object required"})
                return
            media = [{"type": "image", "path": p} for p in req.get("images") or []]
            started = time.perf_counter()
            with _lock:
                model = ensure_model()
                result = model.system_one(req.get("state"), questions, media or None)
            result["model"] = MODEL
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.info("decision ok in %.0fms (%d questions)", elapsed_ms, len(questions))
            try:
                with USAGE_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": int(time.time()),
                        "model": MODEL,
                        "questions": len(questions),
                        "latency_ms": round(elapsed_ms),
                        "usage": result.get("usage"),
                    }) + "\n")
            except OSError:
                log.warning("failed to append to %s", USAGE_LOG)
            self._send(200, result)
        except (ValueError, OSError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("decision failed")
            self._send(500, {"error": f"decision failed: {exc}"})

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True, "model": MODEL, "loaded": _model is not None})
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    ensure_model()  # preload at startup; launchd restarts on failure
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    log.info("d1 system_one server on http://%s:%d/v1/decisions", LISTEN_HOST, LISTEN_PORT)
    server.serve_forever()
