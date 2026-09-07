import assert from "node:assert/strict";
import test from "node:test";
import { isCapabilities, isQueryResponse, queryDecision, safeMessage } from "../lib/contracts";
import { cellText, governanceEvidence, parseSchema, resultsCsv } from "../lib/presentation";
import { ApiError, getCapabilities, getHealth, runQuery } from "../lib/api";
import type { Capabilities, QueryResponse } from "../lib/types";

const capabilities: Capabilities = {
  app_mode: "public_demo", max_result_rows: 200, default_provider: "demo", allows_free_text: false,
  providers: [{ provider: "demo", model_name: "secure-demo-generator", requires_api_key: false, supports_host: false }],
  roles: [{ role: "sales_analyst", requires_employee_id: false, example_questions: ["List products"] }],
  role: "sales_analyst", schema: "Database schema:\n\nTable: products\nColumns:\n- product_id (INTEGER, primary key)\n- product_name (TEXT, nullable)",
  ollama_host: null,
};
const authorized: QueryResponse = {
  success: true, question: "List products", provider: "demo", model: "secure-demo-generator", role: "sales_analyst",
  generated_sql: "SELECT product_id FROM products", authorized_sql: "SELECT product_id FROM products", explanation: "Lists IDs.",
  rows: [{ product_id: 1 }], row_count: 1, truncated: false, row_level_security_applied: false,
  guardrail_checks: ["Statement is a read-only query expression", "Exactly one statement detected",
    "SQL parsed successfully with SQLGlot SQLite dialect", "Referenced physical tables are allowlisted"],
  rbac_checks: ["Column access validated", "No row-level filter required"], error_type: null, error_message: null,
};
const blocked: QueryResponse = { ...authorized, success: false, generated_sql: "", authorized_sql: "", explanation: "", rows: [], row_count: 0,
  guardrail_checks: [], rbac_checks: [], error_type: "access_denied", error_message: "The query is not allowed for this user role." };

test("capabilities match the backend contract and reject missing/inconsistent scope", () => {
  assert.ok(isCapabilities(capabilities));
  assert.ok(!isCapabilities({ ...capabilities, schema: null }));
  assert.ok(!isCapabilities({ ...capabilities, roles: [] }));
  assert.ok(!isCapabilities({ ...capabilities, default_provider: "openai" }));
  assert.ok(!isCapabilities({ ...capabilities, max_result_rows: -1 }));
});
test("valid authorized, empty, and blocked responses are accepted", () => {
  assert.ok(isQueryResponse(authorized)); assert.ok(isQueryResponse(blocked));
  assert.ok(isQueryResponse({ ...authorized, rows: [], row_count: 0 }));
});

test("guardrail library labels are optional backend metadata within the request catalog", () => {
  const question = "Delete all customers from the database.";
  const entry = { ...capabilities.roles[0], example_questions: [question], guardrail_test_questions: [question] };
  assert.ok(isCapabilities({ ...capabilities, roles: [entry] }));
  assert.ok(!isCapabilities({ ...capabilities, roles: [{ ...entry, guardrail_test_questions: "invalid" }] }));
  assert.ok(!isCapabilities({ ...capabilities, roles: [{ ...entry, guardrail_test_questions: ["Missing request"] }] }));
});

test("curated demo requests are submitted and display only the backend security decision", async () => {
  const previousFetch = globalThis.fetch; const previousEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://backend.test";
  try {
    for (const [question, error_type] of [
      ["Delete all customers from the database.", "unsafe_sql"],
      ["Show all employees and their details.", "access_denied"],
    ]) {
      const body = { question, user_id: "visitor", role: "sales_analyst" as const };
      const backendResponse = { ...blocked, question, error_type };
      let calls = 0;
      globalThis.fetch = async (url, options) => {
        calls++;
        assert.equal(String(url), "/api/query");
        assert.deepEqual(JSON.parse(String(options?.body)), body);
        return Response.json(backendResponse);
      };
      const response = await runQuery(body, new AbortController().signal);
      assert.equal(calls, 1);
      assert.deepEqual(response, backendResponse);
      assert.equal(queryDecision(response).title, "BLOCKED");
      assert.equal(queryDecision({ ...authorized, question }).title, "AUTHORIZED");
    }
  } finally { globalThis.fetch = previousFetch; if (previousEnv === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL; else process.env.NEXT_PUBLIC_API_BASE_URL = previousEnv; }
});
test("malformed and failed responses with rows never become displayable results", () => {
  for (const value of [null, {}, { ...authorized, success: "true" }, { ...authorized, row_count: 2 },
    { ...authorized, authorized_sql: "" }, { ...authorized, error_type: "unsafe_sql" },
    { ...blocked, rows: [{ secret: "forbidden" }], row_count: 1 }, { ...blocked, truncated: true }]) {
    assert.ok(!isQueryResponse(value));
  }
});
test("HTTP-success domain failures are blocked only for actual security failures", () => {
  assert.equal(queryDecision(authorized).title, "AUTHORIZED");
  assert.equal(queryDecision(blocked).title, "BLOCKED");
  assert.equal(queryDecision({ ...blocked, error_type: "provider_error" }).title, "PROVIDER ERROR");
  assert.equal(queryDecision({ ...blocked, error_type: "validation_error" }).title, "VALIDATION ERROR");
});
test("no checkpoint is verified before response evidence", () => {
  assert.ok(governanceEvidence(null, capabilities).every((item) => item.status !== "VERIFIED"));
  assert.ok(governanceEvidence(blocked, capabilities).every((item) => item.status !== "VERIFIED"));
  assert.equal(governanceEvidence(null, capabilities)[7].status, "CAP 200");
});
test("verification is driven by matching backend diagnostics and flags", () => {
  const evidence = governanceEvidence(authorized, capabilities);
  assert.equal(evidence[0].status, "VERIFIED"); assert.equal(evidence[6].status, "NOT REQUIRED");
  const filtered = governanceEvidence({ ...authorized, row_level_security_applied: true, truncated: true }, capabilities);
  assert.equal(filtered[6].status, "APPLIED"); assert.equal(filtered[7].status, "TRUNCATED");
  assert.equal(governanceEvidence({ ...authorized, guardrail_checks: [] }, capabilities)[0].status, "AWAITING EVIDENCE");
});
test("schema parsing is presentation of actual supplied tables and columns only", () => {
  assert.deepEqual(parseSchema(capabilities.schema), [{ name: "products", columns: [
    { name: "product_id", metadata: "INTEGER, primary key" }, { name: "product_name", metadata: "TEXT, nullable" },
  ], foreignKeys: [] }]);
  assert.deepEqual(parseSchema("Database schema:"), []);
});
test("values and CSV keep nulls, booleans, numbers, escaping, and formula safety", () => {
  assert.equal(cellText(null), "NULL"); assert.equal(cellText(false), "false"); assert.equal(cellText(-12.5), "-12.5");
  const csv = resultsCsv([{ name: '=HYPERLINK("x")', count: -2, active: true, value: null }]);
  assert.ok(csv.includes(`"'=HYPERLINK(""x"")"`)); assert.ok(csv.includes('"-2","true",""'));
});
test("raw backend stack traces are never presented", () => {
  assert.equal(safeMessage('Traceback (most recent call last):\nFile "a.py", line 9', "Safe error"), "Safe error");
  assert.equal(safeMessage("Role does not allow this query.", "Safe error"), "Role does not allow this query.");
});

test("API client forwards the exact body once, without cookies or credential URLs", async () => {
  const previousFetch = globalThis.fetch; const previousEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://backend.test";
  const requests: { url: string; options?: RequestInit }[] = [];
  globalThis.fetch = async (url, options) => { requests.push({ url: String(url), options }); return Response.json(authorized); };
  try {
    const response = await runQuery({ question: "List products", user_id: "tester", role: "sales_analyst", api_key: "test-key-only" }, new AbortController().signal);
    assert.deepEqual(response, authorized); assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/api/query"); assert.equal(requests[0].options?.credentials, "omit");
    assert.equal(requests[0].options?.cache, "no-store");
    assert.equal(JSON.parse(String(requests[0].options?.body)).api_key, "test-key-only");
  } finally { globalThis.fetch = previousFetch; if (previousEnv === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL; else process.env.NEXT_PUBLIC_API_BASE_URL = previousEnv; }
});
test("startup, role scope, malformed responses, and offline requests use safe client states", async () => {
  const previousFetch = globalThis.fetch; const previousEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://backend.test";
  try {
    let requestedUrl = "";
    globalThis.fetch = async (url) => { requestedUrl = String(url); return Response.json(capabilities); };
    await getCapabilities(new AbortController().signal, "account_manager", 2);
    assert.equal(requestedUrl, "/api/capabilities?role=account_manager&employee_id=2");
    globalThis.fetch = async () => Response.json({ status: "ok" });
    assert.deepEqual(await getHealth(new AbortController().signal), { status: "ok" });
    globalThis.fetch = async () => Response.json({ success: true, rows: [] });
    await assert.rejects(runQuery({ question: "q", role: "sales_analyst", user_id: "u" }, new AbortController().signal), (error) => error instanceof ApiError && error.kind === "malformed");
    globalThis.fetch = async () => { throw new TypeError("private network detail"); };
    await assert.rejects(getHealth(new AbortController().signal), (error) => error instanceof ApiError && error.kind === "offline" && !error.message.includes("private"));
  } finally { globalThis.fetch = previousFetch; if (previousEnv === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL; else process.env.NEXT_PUBLIC_API_BASE_URL = previousEnv; }
});
