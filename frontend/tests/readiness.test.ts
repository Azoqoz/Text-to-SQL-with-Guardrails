import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { ApiError } from "../lib/api";
import { HEALTH_RETRY_MS, loadReadyCapabilities, WAKE_WINDOW_MS } from "../lib/readiness";

const capabilities = {
  app_mode: "public_demo", max_result_rows: 200, default_provider: "demo", allows_free_text: false,
  providers: [{ provider: "demo", model_name: "secure-demo-generator", requires_api_key: false, supports_host: false }],
  roles: [{ role: "sales_analyst", requires_employee_id: false, example_questions: ["List products"] }],
  role: "sales_analyst", schema: "Table: products", ollama_host: null,
};

function setup(t: TestContext, fetcher: typeof fetch) {
  const previous = process.env.NEXT_PUBLIC_API_BASE_URL;
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://backend.test";
  t.after(() => {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
    else process.env.NEXT_PUBLIC_API_BASE_URL = previous;
  });
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  t.mock.method(globalThis, "fetch", fetcher);
}

// Flush fetch, response decoding, and readiness continuations after a clock tick.
async function flush() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
async function advance(t: TestContext, ms: number) {
  t.mock.timers.tick(ms); await flush();
  t.mock.timers.tick(0); await flush();
}

test("healthy startup probes /health immediately, then loads role capabilities once", async (t) => {
  const events: string[] = [];
  setup(t, async (url) => {
    events.push(String(url));
    return Response.json(String(url) === "/api/health" ? { status: "ok" } : capabilities);
  });
  const result = loadReadyCapabilities(new AbortController().signal, "sales_analyst", undefined, () => events.push("online"));
  assert.deepEqual(events, ["/api/health"]);
  assert.deepEqual(await result, capabilities);
  assert.deepEqual(events, ["/api/health", "online", "/api/capabilities?role=sales_analyst"]);
  await advance(t, WAKE_WINDOW_MS);
  assert.equal(events.length, 3);
});

test("sleeping API retries quietly every 2.5 seconds and automatically loads capabilities", async (t) => {
  const calls: string[] = [];
  let healthCalls = 0; let online = false; let settled = false;
  setup(t, async (url) => {
    calls.push(String(url));
    if (String(url) === "/api/health") {
      healthCalls++;
      if (healthCalls === 1) throw new TypeError("Service sleeping");
      if (healthCalls === 2) return new Response("Starting service", { status: 503 });
      return Response.json({ status: "ok" });
    }
    assert.ok(online);
    return Response.json(capabilities);
  });
  const result = loadReadyCapabilities(new AbortController().signal, undefined, undefined, () => { online = true; });
  void result.then(() => { settled = true; });
  await flush();
  assert.equal(settled, false); assert.equal(online, false);
  await advance(t, HEALTH_RETRY_MS - 1);
  assert.equal(healthCalls, 1);
  await advance(t, 1);
  assert.equal(healthCalls, 2); assert.equal(settled, false);
  await advance(t, HEALTH_RETRY_MS);
  assert.deepEqual(await result, capabilities);
  assert.deepEqual(calls, ["/api/health", "/api/health", "/api/health", "/api/capabilities"]);
});

test("persistent failures become offline only after the entire 90-second window", async (t) => {
  let calls = 0; let failed = false;
  setup(t, async (url) => {
    assert.equal(String(url), "/api/health"); calls++;
    throw new TypeError("Service unavailable");
  });
  const pending = loadReadyCapabilities(new AbortController().signal);
  const rejection = assert.rejects(pending, (error) => {
    failed = true;
    return error instanceof ApiError && error.kind === "offline" && error.message.includes("90 seconds");
  });
  await flush();
  assert.equal(WAKE_WINDOW_MS, 90_000); assert.equal(HEALTH_RETRY_MS, 2_500);
  for (let elapsed = HEALTH_RETRY_MS; elapsed < WAKE_WINDOW_MS; elapsed += HEALTH_RETRY_MS) {
    await advance(t, HEALTH_RETRY_MS);
    assert.equal(failed, false);
  }
  await advance(t, HEALTH_RETRY_MS - 1); assert.equal(failed, false);
  await advance(t, 1); await rejection;
  assert.equal(calls, 36);
  await advance(t, WAKE_WINDOW_MS); assert.equal(calls, 36);
});

test("hanging probes time out without overlap and are cancelled at the wake deadline", async (t) => {
  let active = 0; let calls = 0;
  setup(t, (url, options) => new Promise((_resolve, reject) => {
    assert.equal(String(url), "/api/health"); calls++; active++;
    assert.equal(active, 1);
    options?.signal?.addEventListener("abort", () => {
      active--; reject(new DOMException("Cancelled", "AbortError"));
    }, { once: true });
  }));
  const rejected = assert.rejects(loadReadyCapabilities(new AbortController().signal),
    (error) => error instanceof ApiError && error.kind === "offline");
  for (let elapsed = 0; elapsed < WAKE_WINDOW_MS; elapsed += HEALTH_RETRY_MS) await advance(t, HEALTH_RETRY_MS);
  await rejected;
  assert.equal(active, 0); assert.equal(calls, 36);
});

test("cancelling a retry delay stops polling; reconnect starts a fresh immediate probe", async (t) => {
  let calls = 0; let healthy = false;
  setup(t, async (url) => {
    calls++;
    if (!healthy) throw new TypeError("Sleeping");
    return Response.json(String(url) === "/api/health" ? { status: "ok" } : capabilities);
  });
  const controller = new AbortController();
  const rejected = assert.rejects(loadReadyCapabilities(controller.signal), { name: "AbortError" });
  await flush(); controller.abort(); await rejected;
  await advance(t, WAKE_WINDOW_MS); assert.equal(calls, 1);
  healthy = true;
  assert.deepEqual(await loadReadyCapabilities(new AbortController().signal), capabilities);
  assert.equal(calls, 3);
});

test("unmount cancellation aborts an active probe and never loads capabilities", async (t) => {
  let aborted = false; let calls = 0;
  setup(t, (_url, options) => new Promise((_resolve, reject) => {
    calls++;
    options?.signal?.addEventListener("abort", () => {
      aborted = true; reject(new DOMException("Cancelled", "AbortError"));
    }, { once: true });
  }));
  const controller = new AbortController();
  const rejected = assert.rejects(loadReadyCapabilities(controller.signal), { name: "AbortError" });
  controller.abort(); await rejected;
  await advance(t, WAKE_WINDOW_MS);
  assert.equal(aborted, true); assert.equal(calls, 1);
});

test("capabilities failures after health success remain genuine scope errors", async (t) => {
  let calls = 0;
  setup(t, async (url) => {
    calls++;
    return String(url) === "/api/health" ? Response.json({ status: "ok" })
      : Response.json({ error_message: "Schema unavailable" }, { status: 503 });
  });
  await assert.rejects(loadReadyCapabilities(new AbortController().signal),
    (error) => error instanceof ApiError && error.kind === "server");
  await advance(t, WAKE_WINDOW_MS); assert.equal(calls, 2);
});
