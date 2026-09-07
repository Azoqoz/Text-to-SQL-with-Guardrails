import type { Capabilities, JsonValue, QueryResponse } from "./types";

export interface SchemaTable { name: string; columns: { name: string; metadata: string }[]; foreignKeys: string[] }
// Presentation of the supplied schema text only. Never used to authorize SQL.
export function parseSchema(schema: string): SchemaTable[] {
  const tables: SchemaTable[] = [];
  let current: SchemaTable | undefined;
  let foreignKeys = false;
  for (const line of schema.split(/\r?\n/)) {
    if (line.startsWith("Table: ")) {
      current = { name: line.slice(7), columns: [], foreignKeys: [] };
      tables.push(current); foreignKeys = false;
    } else if (line === "Foreign keys:") foreignKeys = true;
    else if (line === "Columns:") foreignKeys = false;
    else if (current && line.startsWith("- ")) {
      if (foreignKeys) current.foreignKeys.push(line.slice(2));
      else {
        const match = /^- (.+?) \((.*)\)$/.exec(line);
        if (match) current.columns.push({ name: match[1], metadata: match[2] });
      }
    }
  }
  return tables;
}

export function governanceEvidence(result: QueryResponse | null, capabilities: Capabilities | null) {
  // Exact backend diagnostics supply the evidence; no frontend SQL validation.
  const checks = result?.success ? [...result.guardrail_checks, ...result.rbac_checks] : [];
  const verified = (evidence: string) => checks.includes(evidence) ? "VERIFIED" : "AWAITING EVIDENCE";
  return [
    { title: "Read only", status: verified("Statement is a read-only query expression") },
    { title: "Single statement", status: verified("Exactly one statement detected") },
    { title: "SQLGlot AST", status: verified("SQL parsed successfully with SQLGlot SQLite dialect") },
    { title: "Role access", status: verified("Column access validated") },
    { title: "Schema scope", status: capabilities ? "SCOPE LOADED" : "NOT LOADED" },
    { title: "Table allowlist", status: verified("Referenced physical tables are allowlisted") },
    { title: "Row scope", status: result?.success && result.row_level_security_applied ? "APPLIED"
      : checks.includes("No row-level filter required") ? "NOT REQUIRED" : "AWAITING EVIDENCE" },
    { title: "Result limit", status: result?.success && result.truncated ? "TRUNCATED"
      : capabilities ? `CAP ${capabilities.max_result_rows}` : "NOT LOADED" },
  ];
}

export function cellText(value: JsonValue | undefined): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function resultsCsv(rows: Record<string, JsonValue>[]): string {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const escape = (value: JsonValue | undefined) => {
    let text = value == null ? "" : cellText(value);
    // CSV formula safety applies to strings, not negative numeric values.
    if (typeof value === "string" && /^[\s]*[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  return [columns.map(escape).join(","), ...rows.map((row) => columns.map((column) => escape(row[column])).join(","))].join("\r\n");
}
