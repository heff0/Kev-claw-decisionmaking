// d1-local: OpenClaw decision provider backed by a local d1-Omni (MLX system_one) runner.
// Speaks the host decision contract and translates to the runner's System One wire dialect.
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const PLUGIN_ID = "d1-local";
const DEFAULT_BASE_URL = "http://127.0.0.1:8090/v1/decisions";
const DEFAULT_TIMEOUT_MS = 15000;

// One-time operator hint when the runner is unreachable (gateway logs).
let failureHintShown = false;
function hintTransportFailure(baseUrl) {
  if (failureHintShown) return;
  failureHintShown = true;
  console.error(
    `[d1-local] local decision runner unreachable at ${baseUrl}. ` +
      `Check it with: curl -s http://127.0.0.1:8090/health ` +
      `(restart services with scripts/install-services.sh; first-time setup: scripts/setup.sh).`
  );
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
  return { baseUrl, timeoutMs };
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
  return {
    id: PLUGIN_ID,
    contractVersion: 1,
    isReady: () => true,
    async evaluate(batch, context) {
      context.signal.throwIfAborted();
      const config = getConfig();
      const remaining = context.deadlineMonotonicMs - performance.now();
      if (remaining <= 0) return { status: "unavailable", reason: "transport" };

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
          hintTransportFailure(config.baseUrl);
          return { status: "unavailable", reason: "transport" };
        }
        failureHintShown = false;
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
        hintTransportFailure(config.baseUrl);
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
      },
    },
  },
  register(api) {
    api.registerDecisionProvider(createDecisionProvider(() => resolveConfig(api.runtime.config.current())));
  },
});
