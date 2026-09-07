export const ROLES = ["sales_analyst", "sales_manager", "account_manager"] as const;
export type Role = typeof ROLES[number];
export const PROVIDERS = ["demo", "openai", "gemini", "anthropic", "ollama"] as const;
export type Provider = typeof PROVIDERS[number];
export type Mode = "public_demo" | "local_full";
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface ProviderCapability {
  provider: Provider;
  model_name: string;
  requires_api_key: boolean;
  supports_host: boolean;
}
export interface RoleCapability {
  role: Role;
  requires_employee_id: boolean;
  example_questions: string[];
  guardrail_test_questions?: string[];
}
export interface Capabilities {
  app_mode: Mode;
  max_result_rows: number;
  default_provider: Provider;
  allows_free_text: boolean;
  providers: ProviderCapability[];
  roles: RoleCapability[];
  role: Role;
  schema: string;
  ollama_host: string | null;
}
export interface QueryRequest {
  question: string;
  user_id: string;
  role: Role;
  employee_id?: number;
  provider?: Provider;
  model_name?: string;
  api_key?: string;
  ollama_host?: string;
}
export interface QueryResponse {
  success: boolean;
  question: string;
  provider: string;
  model: string;
  role: Role;
  generated_sql: string;
  authorized_sql: string;
  explanation: string;
  rows: Record<string, JsonValue>[];
  row_count: number;
  truncated: boolean;
  row_level_security_applied: boolean;
  guardrail_checks: string[];
  rbac_checks: string[];
  error_type: string | null;
  error_message: string | null;
}
export const roleLabel = (role: string) => role.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const PROVIDER_LABELS: Record<string, string> = { demo: "Secure Demo", openai: "OpenAI", gemini: "Gemini", anthropic: "Anthropic", ollama: "Ollama" };
export const providerLabel = (provider: string) => PROVIDER_LABELS[provider] ?? provider;
