import { isCapabilities, isQueryResponse, safeMessage } from "./contracts";
import type { QueryRequest, Role } from "./types";

export type IssueKind = "offline" | "malformed" | "validation" | "server" | "configuration" | "timeout";
export class ApiError extends Error {
  constructor(public kind: IssueKind, message: string) { super(message); }
}

async function request<T>(path: string, guard: (v: unknown) => v is T, signal: AbortSignal, body?: QueryRequest): Promise<T> {
  if (!process.env.NEXT_PUBLIC_API_BASE_URL?.trim()) {
    throw new ApiError("configuration", "Set NEXT_PUBLIC_API_BASE_URL in the frontend environment, then restart the frontend.");
  }
  let response: Response;
  try {
    response = await fetch(`/api/${path}`, {
      method: body ? "POST" : "GET", cache: "no-store", credentials: "omit",
      headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.any([signal, AbortSignal.timeout(body ? 180000 : 15000)]),
    });
  } catch {
    if (signal.aborted) throw new DOMException("Request cancelled", "AbortError");
    throw new ApiError("offline", body
      ? "The API connection was interrupted or timed out. The backend may still be finishing. Reconnect before retrying."
      : "The API is not responding. Check that the backend is running, then reconnect.");
  }
  let value: unknown;
  try { value = await response.json(); } catch {
    throw new ApiError(response.ok ? "malformed" : "offline", response.ok
      ? "The API returned an unreadable response. No result has been accepted."
      : "The API is unavailable. Check the backend connection and reconnect.");
  }
  if (!response.ok) {
    const error = typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
    throw new ApiError(response.status === 422 ? "validation" : "server",
      safeMessage(error.error_message, "The API could not complete the request. Please retry."));
  }
  if (!guard(value)) throw new ApiError("malformed", "The API response did not match the expected contract. No result has been accepted.");
  return value;
}

export const getHealth = (signal: AbortSignal) => request("health", (v): v is { status: "ok" } =>
  typeof v === "object" && v !== null && "status" in v && v.status === "ok", signal);
export const getCapabilities = (signal: AbortSignal, role?: Role, employeeId?: number) => {
  const params = new URLSearchParams();
  if (role) params.set("role", role);
  if (employeeId !== undefined) params.set("employee_id", String(employeeId));
  return request(`capabilities${params.size ? `?${params}` : ""}`, isCapabilities, signal);
};
export const runQuery = (body: QueryRequest, signal: AbortSignal) => request("query", isQueryResponse, signal, body);
