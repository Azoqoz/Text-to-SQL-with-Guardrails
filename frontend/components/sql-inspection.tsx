"use client";

import { useMemo, useState } from "react";
import { format } from "sql-formatter";
import type { QueryResponse } from "@/lib/types";

function highlight(line: string) {
  const parts = line.split(/('(?:''|[^'])*'|"(?:""|[^"])*"|\b(?:SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|WITH|AS|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|CASE|WHEN|THEN|ELSE|END|AND|OR|NOT|IN|IS|NULL|DESC|ASC|SUM|COUNT|ROUND|SUBSTR|AVG|MIN|MAX)\b|\b\d+(?:\.\d+)?\b)/gi);
  return parts.map((part, i) => <span key={i} className={i % 2 === 0 ? undefined
    : part.startsWith("'") || part.startsWith('"') ? "sql-string" : /^\d/.test(part) ? "sql-number" : "sql-keyword"}>{part}</span>);
}

export function SqlInspection({ result, busy }: { result: QueryResponse | null; busy: boolean }) {
  const [version, setVersion] = useState<"authorized" | "generated">("authorized");
  const [copyState, setCopyState] = useState("");
  const sql = result ? version === "authorized" ? result.authorized_sql : result.generated_sql : "";
  const formatted = useMemo(() => {
    if (!sql) return "";
    try { return format(sql, { language: "sqlite", keywordCase: "upper", tabWidth: 2 }); }
    catch { return sql; }
  }, [sql]);
  async function copy() {
    try { await navigator.clipboard.writeText(sql); setCopyState("Copied"); }
    catch { setCopyState("Copy unavailable — select the SQL to copy"); }
  }
  return <section className="inspection-stage" aria-labelledby="inspection-title">
    <div className="stage-heading inspection-caption"><span className="eyebrow"><span className="section-index">03</span> INSPECT</span>
      <h2 id="inspection-title">SQL, in <br />plain sight.</h2><p>Inspect the generated statement and the SQL authorized by the backend, then review the result.</p>
      <div className="inspection-note"><span className="small-cross" aria-hidden>＋</span><span>Read-only inspection.<br />Only the backend executes SQL.</span></div>
      {result?.explanation && <p className="query-explanation">{result.explanation}</p>}
    </div>
    <div className="sql-surface">
      <div className="sql-toolbar"><div className="sql-switch" aria-label="SQL version">
        <button type="button" aria-pressed={version === "authorized"} onClick={() => { setVersion("authorized"); setCopyState(""); }}>Authorized SQL</button>
        <button type="button" aria-pressed={version === "generated"} onClick={() => { setVersion("generated"); setCopyState(""); }}>Generated SQL</button>
      </div><button className="text-button copy-button" disabled={!sql || busy} onClick={copy}>Copy SQL <span aria-hidden>⧉</span></button></div>
      {formatted ? <pre className="sql-code" tabIndex={0} aria-label={`${version} SQL`}><code>{formatted.split("\n").map((line, i) =>
        <span className="sql-line" data-line={i + 1} key={i}>{highlight(line)}{"\n"}</span>)}</code></pre>
        : <div className="sql-empty"><div className="sql-empty-mark" aria-hidden>⌜<span>SQL</span>⌟</div>
          <p>{busy ? "Waiting for the backend’s inspected SQL…" : result ? "No SQL returned for this view." : "Your statement will appear here."}</p>
          <span>{busy ? "Generation, validation, authorization, and execution are processed together."
            : result ? "Rejected SQL is shown only when the backend includes it." : "Submit a request to begin generation and inspection."}</span></div>}
      <div className="sql-footer"><span>{result?.model ? `${result.provider} / ${result.model}` : "SQLITE DIALECT"}</span>
        <span role="status">{copyState || (sql ? `${formatted.split("\n").length} lines · display formatting only` : "NO STATEMENT")}</span></div>
    </div>
  </section>;
}
