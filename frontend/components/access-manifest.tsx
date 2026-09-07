"use client";

import { useState } from "react";
import { parseSchema } from "@/lib/presentation";
import { roleLabel, type Capabilities } from "@/lib/types";

export function AccessManifest({ capabilities, loading, employeeId }: {
  capabilities: Capabilities | null; loading: boolean; employeeId?: number;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const tables = capabilities && !loading ? parseSchema(capabilities.schema) : [];
  const active = tables.find((table) => table.name === selected) ?? tables[0];
  return <section className="access-strip" aria-labelledby="access-title">
    <details>
      <summary>
        <span className="access-title" id="access-title"><span className="section-index">02</span> ACCESS MANIFEST</span>
        <span className="scope-name">{loading ? "Loading access scope…" : capabilities ? roleLabel(capabilities.role) : "Scope unavailable"}</span>
        <span className="table-tokens">{tables.map((table) => <code key={table.name}>{table.name}</code>)}</span>
        <span className="manifest-action">Inspect schema <span aria-hidden>＋</span></span>
      </summary>
      <div className="manifest-body">
        {!capabilities || loading ? <p className="muted">Connect to the API to load the selected role’s schema.</p> : <>
          <div className="table-navigation" aria-label="Accessible tables">
            <span className="eyebrow">{tables.length} tables in supplied scope</span>
            {tables.map((table) => <button type="button" key={table.name} aria-pressed={table.name === active?.name}
              onClick={() => setSelected(table.name)}><code>{table.name}</code><span>{table.columns.length} columns ↗</span></button>)}
          </div>
          <div className="column-manifest">
            {active ? <><div className="manifest-heading"><code>{active.name}</code><span>Backend-provided columns</span></div>
              <dl>{active.columns.map((column) => <div key={column.name}><dt><code>{column.name}</code></dt><dd>{column.metadata}</dd></div>)}</dl>
              {active.foreignKeys.length > 0 && <div className="foreign-keys"><span className="eyebrow">Relationships in scope</span>
                {active.foreignKeys.map((key) => <code key={key}>{key}</code>)}</div>}</> : <p>No table metadata is available for this scope.</p>}
          </div>
          <div className="scope-note"><span className="eyebrow">Row scope</span><p>{employeeId !== undefined
            ? `Employee ID ${employeeId} is supplied to the backend.` : "The selected role is supplied to the backend."}</p>
            <p>Row-filter application is reported with each completed query. Hidden permissions are not inferred.</p>
            <details className="raw-schema"><summary>View original schema text</summary><pre>{capabilities.schema}</pre></details>
          </div>
        </>}
      </div>
    </details>
  </section>;
}
