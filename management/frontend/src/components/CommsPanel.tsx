import type { AgentComm } from "../api";

// Typed agent-comms panel (#4834 comms Phase 1, design §9.4): replaces the raw-JSON `ListPanel`
// render in App.tsx with a structured row. Shows the SERVER-DERIVED sender (from_handle), target,
// kind, and text — plus a host-observed badge so host_observed events read as ground truth, not a
// replica claim. The trust field rides on the row for kinds that carry it (health.observed).
export function CommsPanel({
  items,
  error,
  loading,
}: {
  items: AgentComm[] | undefined;
  error: string | null;
  loading: boolean;
}) {
  return (
    <section className="card">
      <h2>Agent comms</h2>
      {error ? (
        <p className="err">error: {error}</p>
      ) : !items ? (
        <p className="muted">{loading ? "loading…" : "no data"}</p>
      ) : items.length === 0 ? (
        <p className="muted">none yet</p>
      ) : (
        <ul className="list">
          {items.map((c, i) => {
            const trust = typeof c.trust === "string" ? c.trust : undefined;
            return (
              <li key={c.id ?? i} data-testid="comm-row">
                <span className="badge">{c.kind ?? "message"}</span>
                {trust && (
                  <span className={`badge ${trust === "host_observed" ? "ok" : "warn"}`}>
                    {trust === "host_observed" ? "host-observed" : trust}
                  </span>
                )}{" "}
                <strong>{c.from_handle ?? "—"}</strong>
                <span className="muted"> → {c.to_handle ?? "—"}</span>
                {c.text ? <span> · {c.text}</span> : null}
                {c.ts ? <span className="muted small"> · {c.ts}</span> : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
