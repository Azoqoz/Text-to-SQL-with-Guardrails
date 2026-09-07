"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main className="fatal-state"><span className="eyebrow">QUERY FOUNDRY / WORKSPACE</span>
    <h1>The workspace was interrupted.</h1><p>Your credentials have not been saved. Reload the workspace to reconnect.</p>
    <button className="primary-button" onClick={reset}>Reload workspace <span aria-hidden>↗</span></button>
  </main>;
}
