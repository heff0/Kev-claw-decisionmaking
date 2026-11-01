#!/bin/bash
# Wire-dialect smoke test against the local d1 runner (or the 8009 bridge).
set -euo pipefail
URL="${1:-http://127.0.0.1:8090/v1/decisions}"
curl -s "$URL" -H 'Content-Type: application/json' -d '{
  "state": {"message": "Checkout is failing for all customers."},
  "questions": {
    "route": {"type": "choice", "instructions": "Route this request.", "criteria": {"support": "Service failures", "billing": "Payment questions"}},
    "urgency": {"type": "score", "instructions": "How urgent?", "criteria": ["No disruption", "One workflow blocked", "Widespread outage"]},
    "escalate": {"type": "noul", "instructions": "Does this need incident response?", "criteria": {"false": "A routine request can follow normal handling", "true": "Widespread service outages"}}
  }
}' | python3 -m json.tool
