import { useState } from "react";
import { api, type CausalEdge } from "../api";
import { usePoll } from "../hooks";

// Read-only viewer for mined causal principles (BB #764, roadmap surfaced live).
// Each edge is a FUZZY guiding principle mined from mission-loop outcomes, with provenance
// (which run produced it). Formality is EARNED by surprise-driven promotion — nothing here
// is a guaranteed rule yet; the badges reflect that honestly.
export function CausalPanel() {
  const edges = usePoll(api.causalEdges, 8000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function mine() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.mineCausal();
      setMsg(`mined ${r.mined ?? "?"} edge(s) from mission-loop runs`);
      void edges.refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const items: CausalEdge[] = edges.data?.items ?? [];
  const badgeClass = (f?: string) => (f === "formal" ? "ok" : f === "evidential" ? "warn" : "");

  return (
    <section className="card">
      <h2>
        Causal edges <span className="chip">mined principles</span>
      </h2>
      <p className="muted small">
        Fuzzy causal principles mined from real mission-loop outcomes. Formality is <b>earned</b> by
        surprise-driven promotion; nothing here is a guaranteed rule yet.
      </p>
      <button className="gated" disabled={busy} onClick={mine} aria-live="polite">
        {busy ? "mining…" : "Mine principles from runs"}
      </button>
      {msg && <p className="small mono" aria-live="polite">{msg}</p>}
      {edges.error ? (
        <p className="err">error: {edges.error}</p>
      ) : items.length === 0 ? (
        <p className="muted">no causal edges yet — mine from runs</p>
      ) : (
        <ul className="list">
          {items.map((e, i) => (
            <li key={i}>
              <strong>
                {String(e.cause)} → {String(e.effect)}
              </strong>{" "}
              <span className={`badge ${badgeClass(e.formality)}`}>{e.formality}</span>{" "}
              <span className="badge">{e.directness}</span>{" "}
              <span className="muted small">observed in {e.observed_runs ?? (e.provenance?.length ?? 0)} run(s)</span>
              {(e.support_count ?? 0) > 0 && (
                <span className="muted small"> · earned support {e.support_count}</span>
              )}
              {Array.isArray(e.provenance) && e.provenance[0] && (
                <div className="muted small">
                  from run {e.provenance.map((p) => String(p.run_id)).join(", ")}:{" "}
                  {String(e.provenance[0].insight ?? "").slice(0, 90)}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
