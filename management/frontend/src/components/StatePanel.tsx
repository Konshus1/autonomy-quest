import type { RalphState } from "../api";

// Renders the control state. `backing` is shown as a live badge (in_memory_stub -> postgres
// when v6 lands) — deliberately NOT branched on, per the #4407 contract lock.
export function StatePanel({
  state,
  error,
  loading,
}: {
  state: RalphState | null;
  error: string | null;
  loading: boolean;
}) {
  return (
    <section className="card">
      <h2>Ralph state</h2>
      {error ? (
        <p className="err">error: {error}</p>
      ) : !state ? (
        <p className="muted">{loading ? "loading…" : "no data"}</p>
      ) : (
        <div className="kv">
          <div>
            <span className="muted">backing</span>
            <span className="badge">{state.backing}</span>
          </div>
          <div>
            <span className="muted">merge gate</span>
            <span className="badge warn">{state.merge_gate}</span>
          </div>
          <div>
            <span className="muted">replication override</span>
            <span className={`badge ${state.replication.override_enabled ? "err" : "ok"}`}>
              {state.replication.override_enabled ? "AUTO-APPROVE ON" : "operator-gated"}
            </span>
          </div>
          <div>
            <span className="muted">replication</span>
            <span>
              {state.replication.pending} pending / {state.replication.total} total
            </span>
          </div>
          <div>
            <span className="muted">counts</span>
            <span>
              ws {state.counts.workstreams} · tasks {state.counts.tasks} · comms{" "}
              {state.counts.agent_comms} · merges {state.counts.merge_decisions}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}
