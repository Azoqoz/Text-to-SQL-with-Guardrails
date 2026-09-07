"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, getCapabilities, getHealth, runQuery } from "@/lib/api";
import { providerLabel, roleLabel, type Capabilities, type Provider, type QueryRequest, type QueryResponse, type Role } from "@/lib/types";
import { AccessManifest } from "./access-manifest";
import { GovernanceTrack } from "./governance-track";
import { ResultDeck } from "./result-deck";
import { SqlInspection } from "./sql-inspection";

type Connection = "checking" | "online" | "offline";
const issueFrom = (error: unknown) => error instanceof ApiError ? error : new ApiError("server", "The request could not be completed. Please reconnect and try again.");

export function Foundry() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [scopeLoading, setScopeLoading] = useState(true);
  const [connection, setConnection] = useState<Connection>("checking");
  const [scopeIssue, setScopeIssue] = useState<ApiError | null>(null);
  const [queryIssue, setQueryIssue] = useState<ApiError | null>(null);
  const [role, setRole] = useState<Role | undefined>();
  const [employeeInput, setEmployeeInput] = useState("1");
  const [appliedEmployee, setAppliedEmployee] = useState<number | undefined>();
  const [provider, setProvider] = useState<Provider | undefined>();
  const [model, setModel] = useState("");
  const [host, setHost] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const scopeRequest = useRef<AbortController | null>(null);
  const requestedEmployee = useRef<number | undefined>(undefined);
  const queryRequest = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const sequence = useRef(0);
  const resultRegion = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async (selectedRole?: Role, employeeId?: number) => {
    scopeRequest.current?.abort();
    const controller = new AbortController(); scopeRequest.current = controller;
    requestedEmployee.current = employeeId;
    const requestId = ++sequence.current;
    const [health, scope] = await Promise.allSettled([
      getHealth(controller.signal), getCapabilities(controller.signal, selectedRole, employeeId),
    ]);
    if (controller.signal.aborted || requestId !== sequence.current) return;
    setConnection(health.status === "fulfilled" ? "online" : "offline");
    if (scope.status === "fulfilled") {
      const data = scope.value;
      if (selectedRole && data.role !== selectedRole) {
        setScopeIssue(new ApiError("malformed", "The returned scope does not match the selected role. Reconnect to reload it."));
        setCapabilities(null);
      } else {
        setCapabilities(data); setRole(data.role); setAppliedEmployee(employeeId);
        setProvider(data.default_provider); setModel(data.providers.find((p) => p.provider === data.default_provider)?.model_name ?? "");
        setHost(data.ollama_host ?? "");
        setQuestion((current) => data.allows_free_text || data.roles.find((r) => r.role === data.role)?.example_questions.includes(current) ? current : "");
      }
    } else { setScopeIssue(issueFrom(scope.reason)); setCapabilities(null); }
    if (health.status === "rejected" && scope.status === "fulfilled") setScopeIssue(issueFrom(health.reason));
    setScopeLoading(false);
  }, []);

  useEffect(() => {
    const startup = window.setTimeout(() => { void refresh(); }, 0);
    return () => { window.clearTimeout(startup); scopeRequest.current?.abort(); queryRequest.current?.abort(); };
  }, [refresh]);

  function beginRefresh(selectedRole?: Role, employeeId?: number) {
    setScopeLoading(true); setConnection("checking"); setScopeIssue(null); setQueryIssue(null); setResult(null); setApiKey("");
    void refresh(selectedRole, employeeId);
  }

  const selectedRole = capabilities?.roles.find((r) => r.role === role);
  const selectedProvider = capabilities?.providers.find((p) => p.provider === provider);
  const needsEmployee = selectedRole?.requires_employee_id ?? false;
  const employeeNumber = Number(employeeInput);
  const employeeValid = /^\d+$/.test(employeeInput) && Number.isSafeInteger(employeeNumber) && employeeNumber >= 1;
  const scopePending = needsEmployee && (!employeeValid || appliedEmployee !== employeeNumber);
  const examples = selectedRole?.example_questions ?? [];
  const local = capabilities?.app_mode === "local_full";
  const freeText = capabilities?.allows_free_text ?? false;
  const hostValid = !selectedProvider?.supports_host || /^https?:\/\/\S+$/i.test(host);
  const keyRequired = Boolean(local && selectedProvider?.requires_api_key);
  const ready = Boolean(capabilities && !scopeLoading && !scopeIssue && connection === "online" && !scopePending && selectedProvider);
  const canRun = ready && !busy && Boolean(question.trim()) && (freeText || examples.includes(question))
    && (!local || Boolean(model.trim())) && (!keyRequired || Boolean(apiKey.trim())) && hostValid;

  function changeRole(next: Role) {
    setRole(next); setQuestion(""); setResult(null); setApiKey("");
    const requiresId = capabilities?.roles.find((r) => r.role === next)?.requires_employee_id;
    const id = requiresId ? employeeValid ? employeeNumber : 1 : undefined;
    if (id !== undefined) setEmployeeInput(String(id));
    beginRefresh(next, id);
  }
  function changeProvider(next: Provider) {
    setProvider(next); setModel(capabilities?.providers.find((p) => p.provider === next)?.model_name ?? "");
    setApiKey(""); setResult(null); setQueryIssue(null);
  }
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canRun || inFlight.current || !role) return;
    inFlight.current = true; setBusy(true); setResult(null); setQueryIssue(null);
    const controller = new AbortController(); queryRequest.current = controller;
    const payload: QueryRequest = {
      question, role, user_id: `foundry_${role}${needsEmployee ? `_${employeeNumber}` : ""}`,
      ...(needsEmployee ? { employee_id: employeeNumber } : {}),
      ...(local ? { provider, model_name: model, ...(selectedProvider?.supports_host ? { ollama_host: host } : {}) } : {}),
      ...(keyRequired ? { api_key: apiKey } : {}),
    };
    // Remove the credential from React state immediately. No browser storage or history.
    setApiKey("");
    try {
      const response = await runQuery(payload, controller.signal);
      if (controller.signal.aborted) return;
      if (response.role !== role || response.question !== question) {
        throw new ApiError("malformed", "The query response does not match the submitted request. No result has been accepted.");
      }
      setResult(response); setConnection("online");
      requestAnimationFrame(() => resultRegion.current?.focus({ preventScroll: true }));
    } catch (error) {
      if (!controller.signal.aborted) {
        const issue = issueFrom(error); setQueryIssue(issue);
        if (issue.kind === "offline" || issue.kind === "timeout") setConnection("offline");
      }
    } finally {
      delete payload.api_key;
      inFlight.current = false;
      if (!controller.signal.aborted) { setBusy(false); setApiKey(""); }
    }
  }

  const activeIssue = queryIssue ?? scopeIssue;
  const issueTitle = activeIssue ? ({ offline: "API OFFLINE", malformed: "MALFORMED RESPONSE", validation: "VALIDATION ERROR",
    server: "API ERROR", configuration: "CONNECTION NOT CONFIGURED", timeout: "CONNECTION TIMED OUT" })[activeIssue.kind] : "";
  const hint = scopeLoading ? "Loading capabilities and access scope…" : !capabilities ? "Connect to the API to load requests and access scope."
    : connection !== "online" ? "Reconnect to the API before running another request."
    : scopePending ? "Apply a valid employee ID to refresh the access scope." : !question ? freeText ? "Enter a request or choose an example." : "Choose a request from the library."
    : keyRequired && !apiKey ? "Enter a provider key for this request. It will be cleared on submission."
    : local && !model.trim() ? "Enter the model to use for generation." : !hostValid ? "Enter a valid HTTP or HTTPS Ollama host."
    : "Ready for backend inspection and authorization.";

  return <>
    <a className="skip-link" href="#request-input">Skip to request</a>
    <header className="system-rail">
      <a href="#" className="brand" aria-label="Query Foundry home"><span className="brand-mark" aria-hidden><i /><i /><i /></span><span>QUERY <b>FOUNDRY</b></span></a>
      <div className="rail-context"><span>ENVIRONMENT <strong>{capabilities ? local ? "Local / Full" : "Public / Demo" : "Unresolved"}</strong></span>
        <span>ROLE <strong>{role ? roleLabel(role) : "Pending scope"}</strong></span>
        <span>PROVIDER <strong>{provider ? providerLabel(provider) : "Unresolved"}</strong></span></div>
      <div className={`connectivity ${connection}`} role="status"><i aria-hidden />API {connection === "checking" ? "connecting" : connection}</div>
    </header>
    <main className="foundry-shell">
      <div className="workspace-heading"><div><span className="eyebrow">CONTROLLED DATABASE ACCESS</span><h1>Request. Inspect. Authorize.</h1></div>
        <div className="workflow-ruler" aria-label="Backend workflow"><span>REQUEST</span><i aria-hidden>→</i><span>GENERATE</span><i aria-hidden>→</i><span>INSPECT</span><i aria-hidden>→</i><span>GOVERN</span><i aria-hidden>→</i><span>EXECUTE</span></div></div>
      {activeIssue && <div className="issue-banner" role="alert"><span className="issue-symbol" aria-hidden>!</span><div><strong>{issueTitle}</strong><p>{activeIssue.message}</p></div>
        <button className="secondary-button" disabled={busy || scopeLoading} onClick={() => beginRefresh(role, requestedEmployee.current)}>Reconnect <span aria-hidden>↻</span></button></div>}
      <section className="request-stage" aria-labelledby="request-title">
        <div className="request-section-rail"><span><span className="section-index">01</span> REQUEST STAGE</span><span>{capabilities ? freeText ? "NATURAL LANGUAGE INPUT" : "CURATED REQUESTS / OFFLINE GENERATION" : "AWAITING CAPABILITIES"}</span></div>
        <form onSubmit={submit} autoComplete="off">
          <div className="control-strip">
            <label className="field role-field"><span>Access role</span><select aria-label="Access role" value={role ?? ""} disabled={busy || scopeLoading || !capabilities}
              onChange={(event) => changeRole(event.target.value as Role)}>
              {!capabilities && <option value="">Awaiting access scope</option>}{capabilities?.roles.map((r) => <option key={r.role} value={r.role}>{roleLabel(r.role)}</option>)}</select></label>
            {needsEmployee && <div className="employee-control"><label className="field"><span>Employee ID</span><input aria-label="Employee ID" type="number" min="1" step="1" value={employeeInput}
              disabled={busy || scopeLoading} onChange={(event) => { setEmployeeInput(event.target.value); setResult(null); setApiKey(""); }} /></label>
              <button type="button" className="text-button" disabled={!scopePending || !employeeValid || busy || scopeLoading}
                onClick={() => beginRefresh(role, employeeNumber)}>Apply scope ↗</button></div>}
            {local && <><label className="field"><span>Generation provider</span><select value={provider ?? ""} disabled={busy || scopeLoading} onChange={(event) => changeProvider(event.target.value as Provider)}>
              {capabilities.providers.map((p) => <option key={p.provider} value={p.provider}>{providerLabel(p.provider)}</option>)}</select></label>
              <label className="field model-field"><span>Model</span><input value={model} disabled={busy || scopeLoading} onChange={(event) => setModel(event.target.value)} autoComplete="off" spellCheck={false} /></label></>}
            {!local && <div className="mode-context"><span className="context-cross" aria-hidden>＋</span><p>{capabilities ? "Deterministic generation. The same backend guardrails." : "Capabilities determine the available input and access scope."}</p></div>}
          </div>
          {local && (keyRequired || selectedProvider?.supports_host) && <div className="credential-strip">
            {keyRequired && <label className="field"><span>Provider API key <small>Used once · never saved</small></span><input type="password" name="provider-credential" value={apiKey}
              onChange={(event) => setApiKey(event.target.value)} disabled={busy || scopeLoading} autoComplete="new-password" spellCheck={false} autoCapitalize="none"
              data-lpignore="true" data-1p-ignore="true" aria-describedby="credential-note" /></label>}
            {selectedProvider?.supports_host && <label className="field"><span>Ollama host</span><input type="url" value={host} onChange={(event) => setHost(event.target.value)} disabled={busy || scopeLoading} spellCheck={false} /></label>}
            <p id="credential-note">{keyRequired ? "The key is held only for this request and cleared when you run it." : "Generation uses your configured Ollama server. No API key is required."}</p>
          </div>}
          <div className="request-workspace"><div className="request-composer">
            <div className="composer-heading"><h2 id="request-title">What do you need to know?</h2><span className="request-mode">{freeText ? "FREE TEXT" : "SELECT A REQUEST"}</span></div>
            <label className="sr-only" htmlFor="request-input">Natural-language request</label>
            <textarea id="request-input" value={question} onChange={(event) => setQuestion(event.target.value)} readOnly={!freeText} disabled={busy || scopeLoading || !capabilities}
              aria-describedby="request-hint" placeholder={freeText ? "Describe the data you want to retrieve…" : "Choose a request from the library →"}
              onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); void submit(); } }} />
            <div className="composer-bottom"><div><p id="request-hint" role="status">{busy ? "Generating and executing through the backend. This can take a moment." : hint}</p>
              <span className="keyboard-hint"><kbd>Ctrl</kbd> / <kbd>⌘</kbd> + <kbd>Enter</kbd> to run</span></div>
              <button type="submit" className="primary-button" disabled={!canRun}>{busy ? <><span className="spinner" />Processing</> : <>Run query <span aria-hidden>↗</span></>}</button></div>
          </div><aside className="request-library" aria-label="Backend-provided example requests"><div className="library-heading"><span className="eyebrow">REQUEST LIBRARY</span><span>{String(examples.length).padStart(2, "0")}</span></div>
            <p>{freeText ? "A starting point for your next query." : "Available for the selected access role."}</p>
            {scopeLoading ? <div className="library-empty"><span className="spinner" /> Loading supported requests…</div>
              : examples.length ? <ol>{examples.map((example, i) => <li key={example}><button type="button" disabled={busy || !ready} aria-pressed={question === example}
                onClick={() => { setQuestion(example); setQueryIssue(null); }}><span className="example-index">{String(i + 1).padStart(2, "0")}</span><span>{example}</span><span aria-hidden>↗</span></button></li>)}</ol>
              : <div className="library-empty">{capabilities ? "No example requests supplied." : "Requests will load when the API is connected."}</div>}
          </aside></div>
        </form>
      </section>
      <AccessManifest capabilities={scopePending ? null : capabilities} loading={scopeLoading} employeeId={appliedEmployee} />
      <SqlInspection key={result ? `${result.question}-${result.authorized_sql}` : busy ? "busy" : "idle"} result={result} busy={busy} />
      <GovernanceTrack result={result} capabilities={scopeLoading || scopePending ? null : capabilities} />
      <div ref={resultRegion} tabIndex={-1} className="result-focus"><ResultDeck result={result} busy={busy} maxRows={capabilities?.max_result_rows} /></div>
      <footer className="workspace-footer"><span>QUERY FOUNDRY <i aria-hidden>/</i> REQUEST TO RESULT</span><span>Authorization is decided by the backend.</span><a href="#request-input">Back to request ↑</a></footer>
    </main>
  </>;
}
