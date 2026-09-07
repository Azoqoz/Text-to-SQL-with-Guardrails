import { PROVIDERS, ROLES, type Capabilities, type JsonValue, type QueryResponse } from "./types";

const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");
const nullableString = (v: unknown) => v === null || typeof v === "string";
const role = (v: unknown) => typeof v === "string" && (ROLES as readonly string[]).includes(v);
const provider = (v: unknown) => typeof v === "string" && (PROVIDERS as readonly string[]).includes(v);
const json = (v: unknown): v is JsonValue => v === null || typeof v === "string" || typeof v === "boolean"
  || (typeof v === "number" && Number.isFinite(v)) || (Array.isArray(v) && v.every(json))
  || (record(v) && Object.values(v).every(json));

export function isCapabilities(v: unknown): v is Capabilities {
  if (!record(v) || !["public_demo", "local_full"].includes(String(v.app_mode)) || !role(v.role)
    || !provider(v.default_provider) || typeof v.schema !== "string" || !nullableString(v.ollama_host)
    || typeof v.allows_free_text !== "boolean" || !Number.isInteger(v.max_result_rows)
    || Number(v.max_result_rows) < 1 || Number(v.max_result_rows) > 10000) return false;
  if (!Array.isArray(v.roles) || !v.roles.length || !v.roles.every((r) => record(r) && role(r.role)
    && typeof r.requires_employee_id === "boolean" && strings(r.example_questions))) return false;
  if (!Array.isArray(v.providers) || !v.providers.length || !v.providers.every((p) => record(p) && provider(p.provider)
    && typeof p.model_name === "string" && typeof p.requires_api_key === "boolean" && typeof p.supports_host === "boolean")) return false;
  return v.roles.some((r) => r.role === v.role) && v.providers.some((p) => p.provider === v.default_provider);
}

export function isQueryResponse(v: unknown): v is QueryResponse {
  if (!record(v) || typeof v.success !== "boolean" || !role(v.role)
    || !["question", "provider", "model", "generated_sql", "authorized_sql", "explanation"].every((k) => typeof v[k] === "string")
    || !nullableString(v.error_type) || !nullableString(v.error_message)
    || typeof v.truncated !== "boolean" || typeof v.row_level_security_applied !== "boolean"
    || !strings(v.guardrail_checks) || !strings(v.rbac_checks)
    || !Array.isArray(v.rows) || !v.rows.every((r) => record(r) && Object.values(r).every(json))
    || !Number.isInteger(v.row_count) || v.row_count !== v.rows.length) return false;
  if (v.success) return Boolean(String(v.generated_sql).trim() && String(v.authorized_sql).trim())
    && v.error_type === null && v.error_message === null;
  return v.rows.length === 0 && v.truncated === false && v.row_level_security_applied === false
    && typeof v.error_type === "string" && Boolean(v.error_type)
    && typeof v.error_message === "string" && Boolean(v.error_message);
}

export function safeMessage(value: unknown, fallback: string): string {
  if (typeof value !== "string" || !value.trim() || /Traceback|File ".*", line|\n\s+at\s|<html/i.test(value)) return fallback;
  return value.slice(0, 500);
}

export function queryDecision(result: QueryResponse) {
  if (result.success) return { title: "AUTHORIZED", tone: "authorized", detail: "The backend authorized and executed this query." } as const;
  if (["unsafe_sql", "access_denied"].includes(result.error_type ?? "")) {
    return { title: "BLOCKED", tone: "blocked", detail: "The backend rejected this request. No result rows were returned." } as const;
  }
  const errorTitles: Record<string, string> = {
    provider_error: "PROVIDER ERROR", configuration_error: "CONFIGURATION ERROR",
    invalid_provider_response: "PROVIDER RESPONSE ERROR", validation_error: "VALIDATION ERROR", database_error: "EXECUTION ERROR",
  };
  const title = errorTitles[result.error_type ?? ""] ?? "REQUEST FAILED";
  return { title, tone: "caution", detail: "No authorized result is available for this request." } as const;
}
