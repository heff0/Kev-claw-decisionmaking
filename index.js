// d1-local: OpenClaw decision provider backed by a local d1-Omni (MLX system_one) runner.
// Speaks the host decision contract and translates to the runner's System One wire dialect.
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const PLUGIN_ID = "d1-local";
const DEFAULT_BASE_URL = "http://127.0.0.1:8090/v1/decisions";
const DEFAULT_TIMEOUT_MS = 15000;
const REPO_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

// One-time operator hint when the runner is unreachable (gateway logs).
let failureHintShown = false;
function hintTransportFailure(baseUrl, healAttempted) {
  if (failureHintShown) return;
  failureHintShown = true;
  console.error(
    `[d1-local] local decision runner unreachable at ${baseUrl}. ` +
      `Check it with: curl -s http://127.0.0.1:8090/health or scripts/kev-status.sh ` +
      `(restart services with scripts/install-services.sh; first-time setup: scripts/setup.sh).` +
      (healAttempted
        ? " Self-heal was triggered (scripts/ensure-services.sh); the provider now fails fast and re-probes automatically."
        : ""),
  );
}

// Self-heal: re-bootstrap the launchd units if a reboot, an app-managed update,
// or a LaunchAgent reconcile pass dropped them. Throttled; never blocks the
// decision path.
let lastHealAt = 0;
function healServices(reason, healThrottleMs) {
  const now = Date.now();
  if (now - lastHealAt < healThrottleMs) return false;
  lastHealAt = now;
  try {
    const child = spawn("/bin/bash", [path.join(REPO_ROOT, "scripts", "ensure-services.sh")], {
      stdio: "ignore",
      detached: true,
      env: { ...process.env, KEV_WATCHDOG_REASON: String(reason) },
    });
    child.unref();
    return true;
  } catch {
    return false;
  }
}

// Cached liveness state, updated by real evaluations and by the half-open
// probe inside evaluate(). isReady() itself stays network-free per contract.
const health = { ok: null, checkedAt: 0, inFlight: false };
function probeHealth(baseUrl, healthTimeoutMs) {
  if (health.inFlight) return;
  health.inFlight = true;
  const url = baseUrl.replace(/\/v1\/decisions\/?$/, "/health");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), healthTimeoutMs);
  fetch(url, { signal: controller.signal })
    .then((res) => {
      health.ok = res.ok;
    })
    .catch(() => {
      health.ok = false;
    })
    .finally(() => {
      clearTimeout(timer);
      health.inFlight = false;
      health.checkedAt = Date.now();
    });
}

function resolveConfig(snapshot) {
  const configured = snapshot?.plugins?.entries?.[PLUGIN_ID]?.config ?? {};
  const baseUrl =
    typeof configured.baseUrl === "string" && configured.baseUrl.trim()
      ? configured.baseUrl.trim()
      : DEFAULT_BASE_URL;
  const timeoutMs =
    Number.isFinite(configured.timeoutMs) && configured.timeoutMs > 0
      ? configured.timeoutMs
      : DEFAULT_TIMEOUT_MS;
  const autoHeal = configured.autoHeal !== false;
  const breakerThreshold =
    Number.isFinite(configured.breakerThreshold) && configured.breakerThreshold > 0
      ? configured.breakerThreshold
      : 3;
  const breakerCooldownMs =
    Number.isFinite(configured.breakerCooldownMs) && configured.breakerCooldownMs > 0
      ? configured.breakerCooldownMs
      : 60000;
  const healThrottleMs =
    Number.isFinite(configured.healThrottleMs) && configured.healThrottleMs > 0
      ? configured.healThrottleMs
      : 300000;
  const healthTimeoutMs = Math.min(2000, timeoutMs);
  return { baseUrl, timeoutMs, autoHeal, breakerThreshold, breakerCooldownMs, healThrottleMs, healthTimeoutMs };
}

// Host boolean questions travel to the runner as noul (its native Boolean form).
function toWireQuestions(questions) {
  const out = {};
  for (const [id, q] of Object.entries(questions)) {
    out[id] = q.type === "boolean" ? { ...q, type: "noul" } : q;
  }
  return out;
}

// Runner answers -> host answer shapes. Returns null when the payload is malformed.
function toHostAnswers(evaluation, batchQuestions) {
  const answers = {};
  for (const [id, answer] of Object.entries(evaluation?.answers ?? {})) {
    if (answer?.type === "noul") {
      answers[id] = { type: "boolean", probabilityTrue: answer.noul };
    } else if (answer?.type === "choice") {
      if (typeof answer.choice !== "string" || !answer.probabilities) return null;
      answers[id] = {
        type: "choice",
        choice: answer.choice,
        confidence: answer.confidence,
        probabilities: answer.probabilities,
      };
    } else if (answer?.type === "score") {
      const question = batchQuestions[id];
      if (!question || question.type !== "score") return null;
      answers[id] = {
        type: "score",
        score: answer.score,
        confidence: answer.confidence,
        probabilities: question.criteria.map((_level, i) => answer.probabilities?.[String(i)]),
      };
    } else {
      return null;
    }
  }
  return Object.keys(answers).length > 0 ? answers : null;
}

function createDecisionProvider(getConfig) {
  // Circuit breaker: after repeated transport failures, fail fast for a
  // cooldown window instead of stalling every decision on the full timeout.
  let consecutiveFailures = 0;
  let breakerOpenUntil = 0;

  const noteFailure = (config) => {
    consecutiveFailures += 1;
    if (consecutiveFailures >= config.breakerThreshold) {
      breakerOpenUntil = Date.now() + config.breakerCooldownMs;
      consecutiveFailures = 0;
    }
    const healAttempted = config.autoHeal ? healServices("transport-failure", config.healThrottleMs) : false;
    hintTransportFailure(config.baseUrl, healAttempted);
    return healAttempted;
  };

  return {
    id: PLUGIN_ID,
    contractVersion: 1,
    isReady: () => {
      // Contract: must stay synchronous and network-free. Reads only the state
      // maintained by evaluate(); unknown counts as ready so the first real
      // call still gets a chance.
      return health.ok !== false;
    },
    async evaluate(batch, context) {
      context.signal.throwIfAborted();
      const config = getConfig();
      const remaining = context.deadlineMonotonicMs - performance.now();
      if (remaining <= 0) return { status: "unavailable", reason: "deadline" };

      if (Date.now() < breakerOpenUntil) {
        // Half-open: probe from here (never from isReady) so recovery is
        // noticed without paying the full timeout on every decision.
        if (Date.now() - health.checkedAt > 5000) probeHealth(config.baseUrl, config.healthTimeoutMs);
        return { status: "unavailable", reason: "overloaded" };
      }

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), Math.min(config.timeoutMs, remaining));
      context.signal.addEventListener("abort", () => controller.abort(), { once: true });
      try {
        const response = await fetch(config.baseUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            state: batch.state ?? null,
            questions: toWireQuestions(batch.questions),
          }),
          signal: controller.signal,
        });
        if (!response.ok) {
          noteFailure(config);
          return { status: "unavailable", reason: "transport" };
        }
        failureHintShown = false;
        consecutiveFailures = 0;
        health.ok = true;
        health.checkedAt = Date.now();
        const evaluation = await response.json();
        const answers = toHostAnswers(evaluation, batch.questions);
        if (!answers) return { status: "unavailable", reason: "invalid-response" };
        return {
          status: "ok",
          result: {
            model: evaluation.model ?? PLUGIN_ID,
            answers,
            usage: evaluation.usage
              ? {
                  inputTokens: evaluation.usage.input_tokens,
                  outputTokens: evaluation.usage.output_tokens,
                }
              : undefined,
          },
        };
      } catch {
        context.signal.throwIfAborted();
        noteFailure(config);
        return { status: "unavailable", reason: "transport" };
      } finally {
        clearTimeout(timer);
      }
    },
  };
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "D1 Local (System One)",
  description: "Typed decision provider for a local LiquidAI d1-Omni system_one runner.",
  configSchema: {
    jsonSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        baseUrl: { type: "string", description: "Loopback /v1/decisions endpoint of the local d1 system_one runner." },
        timeoutMs: { type: "number", description: "Per-evaluation HTTP timeout in milliseconds." },
        autoHeal: { type: "boolean", description: "Run scripts/ensure-services.sh when the runner is unreachable or on gateway startup (default true)." },
        breakerThreshold: { type: "number", description: "Consecutive transport failures before the provider fails fast (default 3)." },
        breakerCooldownMs: { type: "number", description: "Fail-fast cooldown window in milliseconds (default 60000)." },
        healThrottleMs: { type: "number", description: "Minimum gap between self-heal attempts in milliseconds (default 300000)." },
      },
    },
  },
  register(api) {
    const getConfig = () => resolveConfig(api.runtime.config.current());
    api.registerDecisionProvider(createDecisionProvider(getConfig));
    // Gateway startup is the one moment we can guarantee a fresh chance to
    // restore the stack, even if launchd lost the units during an update.
    if (getConfig().autoHeal) healServices("gateway-startup", 300000);
  },
});
