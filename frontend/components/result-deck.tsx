"use client";

import { useState } from "react";
import { queryDecision, safeMessage } from "@/lib/contracts";
import { cellText, resultsCsv } from "@/lib/presentation";
import { roleLabel, type QueryResponse } from "@/lib/types";

export function ResultDeck({ result, busy, maxRows }: { result: QueryResponse | null; busy: boolean; maxRows?: number }) {
  if (!result) return <section className="waiting-band" aria-live="polite"><span className="section-index">05</span>
    <strong>{busy ? "PROCESSING REQUEST" : "AWAITING REQUEST"}</strong>
    <span>{busy ? "The backend is processing the full query pipeline." : "Authorization and results will be reported here."}</span>
    {busy && <span className="spinner" aria-label="Processing" />}</section>;
  const decision = queryDecision(result);
  return <section className="result-stage" aria-labelledby="decision-title">
    <div className={`decision-band ${decision.tone}`} role="status"><span className="decision-symbol" aria-hidden>{result.success ? "↗" : decision.tone === "blocked" ? "×" : "!"}</span>
      <div><span className="eyebrow">05 / BACKEND DECISION</span><h2 id="decision-title">{decision.title}</h2></div>
      <div className="decision-context"><strong>{roleLabel(result.role)}</strong><span>{decision.detail}</span></div>
    </div>
    {result.success ? <AuthorizedResults key={`${result.question}-${result.authorized_sql}`} result={result} maxRows={maxRows} />
      : <div className="rejection-surface"><div><span className="eyebrow">REASON RETURNED</span>
          <h3>{safeMessage(result.error_message, "The backend could not complete this request.")}</h3>
          <p className="muted">No result rows are displayed. Review the request and the selected access scope before trying again.</p></div>
        <dl><div><dt>Decision code</dt><dd><code>{result.error_type}</code></dd></div><div><dt>Request</dt><dd>{result.question}</dd></div>
          <div><dt>SQL evidence</dt><dd>{result.generated_sql || result.authorized_sql ? "Available in SQL inspection above" : "Not included in the response"}</dd></div></dl>
      </div>}
  </section>;
}

function AuthorizedResults({ result, maxRows }: { result: QueryResponse; maxRows?: number }) {
  const [page, setPage] = useState(0);
  const columns = [...new Set(result.rows.flatMap((row) => Object.keys(row)))];
  const pageSize = 25;
  const pages = Math.max(1, Math.ceil(result.rows.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = result.rows.slice(current * pageSize, (current + 1) * pageSize);
  function exportCsv() {
    const url = URL.createObjectURL(new Blob(["\uFEFF", resultsCsv(result.rows)], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = "query-foundry-results.csv"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="result-deck">
    <div className="results-heading"><div><span className="eyebrow">RESULT DECK</span><h3>Returned data <span>{result.row_count}</span></h3></div>
      <button className="secondary-button" disabled={!result.rows.length} onClick={exportCsv}>Export CSV <span aria-hidden>↓</span></button></div>
    <p className="executed-question">{result.question}</p>
    <div className="result-notices">
      {result.row_level_security_applied && <p className="row-notice"><span aria-hidden>↳</span> Row-level security was applied by the backend. Review Authorized SQL for the enforced restrictions.</p>}
      {result.truncated && <p className="truncation-notice">RESULT TRUNCATED <span>The backend returned {result.row_count} rows{maxRows ? ` with a configured cap of ${maxRows}` : ""}. Additional rows were not returned.</span></p>}
    </div>
    {result.rows.length ? <><div className="table-scroll" tabIndex={0} role="region" aria-label="Authorized query results">
      <table><caption className="sr-only">Authorized results for {result.question}</caption><thead><tr><th scope="col" className="row-index">#</th>
        {columns.map((column) => <th scope="col" key={column}><code>{column}</code></th>)}</tr></thead>
        <tbody>{visible.map((row, i) => <tr key={current * pageSize + i}><td className="row-index">{current * pageSize + i + 1}</td>
          {columns.map((column) => <td key={column} className={row[column] == null ? "null-value" : typeof row[column] === "number" ? "number-value" : ""}>
            {cellText(row[column])}</td>)}</tr>)}</tbody></table>
    </div><div className="table-footer"><span>Showing {current * pageSize + 1}–{Math.min((current + 1) * pageSize, result.row_count)} of {result.row_count} returned rows</span>
      {pages > 1 && <div className="pagination"><button className="text-button" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous result page">← Previous</button>
        <span>{current + 1} / {pages}</span><button className="text-button" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next result page">Next →</button></div>}
    </div></> : <div className="empty-results"><span aria-hidden>∅</span><h3>Authorized. No matching rows.</h3><p>The query completed successfully. There are no rows to display within its returned scope.</p></div>}
  </div>;
}
