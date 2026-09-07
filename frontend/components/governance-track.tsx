import { governanceEvidence } from "@/lib/presentation";
import type { Capabilities, QueryResponse } from "@/lib/types";

export function GovernanceTrack({ result, capabilities }: { result: QueryResponse | null; capabilities: Capabilities | null }) {
  return <section className="governance-stage" aria-labelledby="governance-title">
    <div className="governance-heading"><h2 id="governance-title"><span className="section-index">04</span> GOVERNANCE TRACK</h2>
      <p>Evidence from the backend. No assumed passes.</p></div>
    <div className="governance-scroll" tabIndex={0} role="region" aria-label="Governance checkpoints">
      <ol className="governance-track">{governanceEvidence(result, capabilities).map((item, i) => {
        const evidenced = ["VERIFIED", "APPLIED"].includes(item.status);
        return <li key={item.title} className={evidenced ? "evidenced" : item.status === "TRUNCATED" ? "caution" : ""}>
          <div><span className="checkpoint-number">{String(i + 1).padStart(2, "0")}</span><span className="checkpoint-mark" aria-hidden>{evidenced ? "✓" : "·"}</span></div>
          <strong>{item.title}</strong><span className="checkpoint-status">{item.status}</span></li>;
      })}</ol>
    </div>
    {result && (result.guardrail_checks.length > 0 || result.rbac_checks.length > 0) && <details className="backend-evidence">
      <summary>Read backend check log <span aria-hidden>＋</span></summary>
      <div><section><h3>Guardrail checks</h3><ol>{result.guardrail_checks.map((check, i) => <li key={i}>{check}</li>)}</ol></section>
        <section><h3>Access checks</h3><ol>{result.rbac_checks.map((check, i) => <li key={i}>{check}</li>)}</ol></section></div>
    </details>}
  </section>;
}
