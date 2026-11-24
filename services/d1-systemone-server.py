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
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Model is operator-controlled: set KEV_MODEL in config/model.env (see
# scripts/set-model.sh) or in the launchd environment. Default below.
MODEL = os.environ.get("KEV_MODEL", "LiquidAI/d1-Omni-2B-MLX-8bit")
USAGE_LOG = Path(__file__).resolve().parent.parent / "logs" / "usage.jsonl"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8090
MAX_BYTES = 64 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("d1-systemone")

_lock = threading.Lock()
_model = None
_processor = None
_started_at = time.time()
_counters = {"decisions": 0, "failures": 0, "last_error": None}


def _load_offline():
    """Load straight from the local HF cache: no network round-trip, so a
    reboot with no internet still brings the decision stack up."""
    from huggingface_hub import snapshot_download
    from mlx_vlm.utils import load

    local = snapshot_download(MODEL, local_files_only=True)
    log.info("using cached model at %s", local)
    return load(local)


def _load_hub():
    from mlx_vlm.utils import load

    return load(MODEL)


def _attempt_load():
    problems = []
    for source, loader in (("local cache", _load_offline), ("huggingface hub", _load_hub)):
        try:
            return loader()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{source}: {type(exc).__name__}: {exc}")
    raise RuntimeError("model load failed (" + " | ".join(problems) + ")")


def ensure_model():
    global _model, _processor
    if _model is not None:
        return _model
    started = time.perf_counter()
    _model, _processor = _attempt_load()
    log.info("loaded %s in %.1fs", MODEL, time.perf_counter() - started)
    # Warm Metal kernels once so the first real decision isn't a cold outlier.
    try:
        warm = {"warm": {"type": "noul", "instructions": "warmup",
                         "criteria": {"false": "no", "true": "yes"}}}
        _model.system_one({"message": "warmup"}, warm, None)
        log.info("warmup decision ok")
    except Exception as exc:  # noqa: BLE001
        log.warning("warmup call failed: %s", exc)
    return _model


def clear_mlx_cache():
    """Bound Metal memory growth after each inference.

    MLX keeps an internal cache of Metal command buffers and intermediate
    allocations that grows with every system_one call. Without flushing, the
    process' memory footprint climbs monotonically over the service lifetime.
    Flushing after each decision trades a small re-allocation cost for
    stable RSS/unified-memory usage.
    """
    try:
        import mlx.core as mx
        mx.clear_cache()
        try:
            mx.set_cache(64 * 1024 * 1024)  # cap at 64 MB
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


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
            clear_mlx_cache()
            log.info("decision ok in %.0fms (%d questions)", elapsed_ms, len(questions))
            _counters["decisions"] += 1
            try:
                with USAGE_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": int(time.time()),
                        "model": MODEL,
                        "status": "ok",
                        "questions": len(questions),
                        "latency_ms": round(elapsed_ms),
                        "usage": result.get("usage"),
                    }) + "\n")
            except OSError:
                log.warning("failed to append to %s", USAGE_LOG)
            self._send(200, result)
        except (ValueError, OSError) as exc:
            _counters["failures"] += 1
            _counters["last_error"] = f"{type(exc).__name__}: {exc}"
            log.warning("decision rejected: %s", _counters["last_error"])
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            _counters["failures"] += 1
            _counters["last_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("decision failed")
            try:
                with USAGE_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": int(time.time()),
                        "model": MODEL,
                        "status": "error",
                        "error": _counters["last_error"],
                    }) + "\n")
            except OSError:
                pass
            self._send(500, {"error": f"decision failed: {exc}"})

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._send(200, {
                "ok": _model is not None,
                "model": MODEL,
                "loaded": _model is not None,
                "uptime_s": round(time.time() - _started_at),
                "decisions": _counters["decisions"],
                "failures": _counters["failures"],
                "last_error": _counters["last_error"],
            })
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    # Preload at startup with bounded backoff so an offline boot or a hub
    # hiccup does not turn into an instant crash-respawn loop hammering the hub.
    attempts = int(os.environ.get("KEV_STARTUP_ATTEMPTS", "3"))
    backoffs = [float(x) for x in os.environ.get("KEV_STARTUP_BACKOFF", "5,25").split(",")]
    for attempt in range(1, attempts + 1):
        try:
            ensure_model()
            break
        except Exception as exc:  # noqa: BLE001
            _counters["last_error"] = f"startup load: {exc}"
            if attempt >= attempts:
                log.error("startup model load failed after %d attempts: %s", attempt, exc)
                raise SystemExit(1)
            delay = backoffs[min(attempt - 1, len(backoffs) - 1)]
            log.warning("startup load attempt %d/%d failed (%s); retrying in %.0fs",
                        attempt, attempts, exc, delay)
            time.sleep(delay)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    log.info("d1 system_one server on http://%s:%d/v1/decisions", LISTEN_HOST, LISTEN_PORT)
    server.serve_forever()
