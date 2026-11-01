#!/usr/bin/env python3
"""Kev decision-usage accounting report.

Reads logs/usage.jsonl (appended by d1-systemone-server.py on every
/v1/decisions call) and reports decision counts, token totals, and the
EQUIVALENT hosted-API cost if the same workload had been billed by
TypeSafe's hosted Jev API (pay-per-input-token; output tokens free).

Reference rates (verified 2026-11-01, docs.typesafe.ai/models; confirm
current terms before relying on them):
  Jev hosted: $0.042 per 1M input tokens, output tokens free.
Use --rate-in / --rate-out to override, and --hardware-cost to get a
simple payback estimate for the local inference box.

Usage:
  kev-usage-report.py [--days N] [--rate-in X] [--rate-out Y]
                      [--hardware-cost Z] [--json]
"""

import argparse
import json
import sys
import time
from pathlib import Path

USAGE_LOG = Path(__file__).resolve().parent.parent / "logs" / "usage.jsonl"

DEFAULT_RATE_IN = 0.042   # $/1M input tokens, hosted Jev
DEFAULT_RATE_OUT = 0.0    # hosted Jev: output tokens free


def _tokens(usage, kind):
    if not isinstance(usage, dict):
        return 0
    for key in (f"{kind}_tokens", f"{kind}Tokens", kind):
        v = usage.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30,
                    help="window in days (0 = all history)")
    ap.add_argument("--rate-in", type=float, default=DEFAULT_RATE_IN,
                    help="$/1M input tokens reference rate")
    ap.add_argument("--rate-out", type=float, default=DEFAULT_RATE_OUT,
                    help="$/1M output tokens reference rate")
    ap.add_argument("--hardware-cost", type=float, default=0.0,
                    help="one-time hardware $ for payback estimate")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if not USAGE_LOG.exists():
        print(f"no usage log yet at {USAGE_LOG}", file=sys.stderr)
        return 1

    now = time.time()
    cutoff = now - args.days * 86400 if args.days > 0 else 0
    calls = qtotal = tin = tout = 0
    first_ts = last_ts = None
    lat = []
    by_day = {}
    total_calls = total_tin = total_tout = 0

    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get("ts") or 0
        i, o = _tokens(rec.get("usage"), "input"), _tokens(rec.get("usage"), "output")
        total_calls += 1
        total_tin += i
        total_tout += o
        if ts < cutoff:
            continue
        calls += 1
        qtotal += rec.get("questions") or 0
        tin += i
        tout += o
        if isinstance(rec.get("latency_ms"), (int, float)):
            lat.append(rec["latency_ms"])
        first_ts = ts if first_ts is None else min(first_ts, ts)
        last_ts = max(last_ts or ts, ts)
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        d = by_day.setdefault(day, {"calls": 0, "in": 0, "out": 0})
        d["calls"] += 1
        d["in"] += i
        d["out"] += o

    span_days = max(1, int((now - first_ts) // 86400) + 1) if first_ts else 0
    cost = tin / 1e6 * args.rate_in + tout / 1e6 * args.rate_out
    monthly_tin = tin / span_days * 30 if span_days else 0
    monthly_cost = monthly_tin / 1e6 * args.rate_in + (tin / span_days * 30 * args.rate_out if span_days else 0) / 1e6
    lifetime_cost = total_tin / 1e6 * args.rate_in + total_tout / 1e6 * args.rate_out

    out = {
        "window_days": args.days,
        "first_recorded": time.strftime("%Y-%m-%d", time.localtime(first_ts)) if first_ts else None,
        "calls": calls,
        "questions": qtotal,
        "input_tokens": tin,
        "output_tokens": tout,
        "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
        "equiv_cost_usd": round(cost, 4),
        "rate_in_per_mtok": args.rate_in,
        "rate_out_per_mtok": args.rate_out,
        "run_rate_monthly_cost_usd": round(monthly_cost, 4),
        "lifetime": {"calls": total_calls, "input_tokens": total_tin,
                     "output_tokens": total_tout,
                     "equiv_cost_usd": round(lifetime_cost, 4)},
        "payback_months": (round(args.hardware_cost / monthly_cost, 1)
                           if args.hardware_cost > 0 and monthly_cost > 0 else None),
        "by_day": by_day,
    }

    if args.json:
        print(json.dumps(out, indent=1))
        return 0

    w = "ALL HISTORY" if args.days == 0 else f"last {args.days} days"
    print(f"Kev decision usage — {w}")
    print(f"  decisions: {calls} ({qtotal} questions, avg latency {out['avg_latency_ms']} ms)")
    print(f"  tokens:    {tin:,} in / {tout:,} out")
    print(f"  equiv API cost @ ${args.rate_in}/Mtok in: ${cost:.4f}")
    print(f"  monthly run-rate equivalent: ${monthly_cost:.4f}/mo")
    print(f"  lifetime: {total_calls} decisions, {total_tin:,} in tokens, ${lifetime_cost:.4f} equiv")
    if out["payback_months"]:
        print(f"  payback on ${args.hardware_cost:.0f} hardware: {out['payback_months']} months")
    return 0


if __name__ == "__main__":
    sys.exit(main())
