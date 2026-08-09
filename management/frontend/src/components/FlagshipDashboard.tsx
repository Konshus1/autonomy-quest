import { useState } from "react";
import type { FlagshipState, ParkedWork } from "../api";
import { api } from "../api";

function value(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

function MissionCard({ state, error, loading }: { state?: FlagshipState | null; error?: string | null; loading: boolean }) {
  const mission = state?.mission;
  const health = state?.health;
  const statusClass = health?.level === "green" ? "ok" : health?.level === "red" ? "err" : "warn";
  const points = [...(state?.trend ?? [])].reverse();
  const max = Math.max(Number(mission?.target ?? 0), ...points.map((p) => Number(p.value)), 1);
  const polyline = points.map((p, i) => `${(i / Math.max(points.length - 1, 1)) * 100},${36 - (Number(p.value) / max) * 32}`).join(" ");
  return <section className="card flagship mission-card" data-testid="flagship-mission">
    <h2>Flagship mission</h2>
    {loading && !state ? <p className="muted">Reading the mission’s real measure…</p> : null}
    {error ? <p className="error-box">Cannot load mission state: {error}</p> : null}
    {state ? <>
      <div className={`health-line ${statusClass}`}><strong>{health?.headline ?? health?.status}</strong><span>{health?.detail}</span></div>
      <h3>{mission?.objective || "Mission not configured"}</h3>
      <div className="measure-grid">
        <div><span className="eyebrow">Current</span><strong className={mission?.error ? "err" : ""}>{mission?.error ? "ERROR" : value(mission?.now)}</strong></div>
        <div><span className="eyebrow">Target</span><strong>{mission?.target_error ? "ERROR" : value(mission?.target)}</strong></div>
        <div><span className="eyebrow">Goal</span><strong>{value(mission?.goal)}</strong></div>
      </div>
      {mission?.error ? <p className="error-box" role="alert">Measure query failed: {mission.error}. This is not zero.</p> : null}
      {mission?.target_error ? <p className="error-box" role="alert">Target query failed: {mission.target_error}</p> : null}
      <p className="mono small">Measure: {value(mission?.measure)}</p>
      <p className="mono small">Latest observation: {value(mission?.latest_measurement?.taken_at)}</p>
      <div className="trend" aria-label="measure over time">
        {points.length ? <svg viewBox="0 0 100 40" preserveAspectRatio="none"><polyline points={polyline} /></svg> : <p className="muted">No recorded measure history yet.</p>}
      </div>
      <div className="flag-row"><span className={`badge ${mission?.satisfied ? "ok" : "warn"}`}>{mission?.satisfied ? "target reached" : "below target"}</span><span className={`badge ${mission?.overshooting ? "err" : "ok"}`}>{mission?.overshooting ? "OVERSHOOT TRIPWIRE" : "not overshooting"}</span><span data-testid="loop-process-status" className={`badge ${state.loop?.process_alive ? "ok" : "err"}`}>loop process: {state.loop?.process_status ?? "stopped"}</span></div>
    </> : null}
  </section>;
}

function CycleCard({ state }: { state?: FlagshipState | null }) {
  return <section className="card flagship" data-testid="cycle-history"><h2>What it did and why</h2>
    <p className="muted small">Persisted before-action rationale beside the observed outcome and recorded cost.</p>
    {!state?.runs?.length ? <p className="empty">No completed cycles yet.</p> : <ol className="timeline">{state.runs.map((r) => <li key={String(r.id)}>
      <div className="timeline-head"><strong>{value(r.summary)}</strong><span className="badge">${Number(r.cost_usd ?? 0).toFixed(4)}</span></div>
      <p><span className="eyebrow">Why before acting</span>{value(r.rationale)}</p>
      <p><span className="eyebrow">Observed outcome</span>{value(r.outcome)}</p>
      <p className="muted small">Measure {value(r.measure_before)} → {value(r.measure_after)} · {r.succeeded ? "succeeded" : "did not succeed"} · {value(r.completed_at)}</p>
    </li>)}</ol>}
  </section>;
}

function LearningsCard({ state }: { state?: FlagshipState | null }) {
  return <section className="card flagship" data-testid="learnings-trail"><h2>What it learned</h2>
    <p className="muted small">Live, unsuperseded learnings available to later cycles. Reflection evidence is unverified unless an independent verifier says otherwise.</p>
    {!state?.learnings?.length ? <p className="empty">No learnings yet — a clean first run starts empty.</p> : <ol className="timeline">{state.learnings.map((l) => <li key={String(l.id)}>
      <strong>{value(l.insight)}</strong><p><span className="eyebrow">Evidence · {l.evidence_kind === "verified_evidence" ? "verified" : "actor claim · unverified"}</span>{value(l.evidence)}</p>
      <p className="muted small">scope {value(l.scope)} · confidence {value(l.confidence)} · proposed {value(l.created_at)}</p>
    </li>)}</ol>}
    {state?.beliefs_revised_count ? <p className="muted">{state.beliefs_revised_count} earlier belief(s) superseded; the trail remains in the database.</p> : null}
  </section>;
}

function GateItem({ item, refresh }: { item: ParkedWork; refresh: () => Promise<unknown> | void }) {
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  async function decide(decision: "approve" | "reject") {
    let token = localStorage.getItem("aqApprovalToken") ?? "";
    if (!token) token = window.prompt("Approval token (docker compose exec app cat /var/run/aq/approval_token)") ?? "";
    if (!token) return;
    localStorage.setItem("aqApprovalToken", token); setBusy(true); setMessage("");
    try { await api.decideGate(item.id, decision, token); setMessage(`${decision}d`); await refresh(); }
    catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <li><div className="timeline-head"><strong>{value(item.summary)}</strong><span className="badge warn">gated</span></div>
    <p>{value(item.rationale)}</p><p className="muted small">expected cost ${Number(item.expected_cost_usd ?? 0).toFixed(2)} · blast radius {value(item.blast_radius)}</p>
    <div className="gate-actions"><button disabled={busy} onClick={() => void decide("approve")}>Approve</button><button disabled={busy} className="reject" onClick={() => void decide("reject")}>Reject</button><span>{message}</span></div>
  </li>;
}

function GateCard({ state, refresh }: { state?: FlagshipState | null; refresh: () => Promise<unknown> | void }) {
  return <section className="card flagship gated" data-testid="gate-queue"><h2>Waiting on a human</h2>
    <p className="muted small">Only plans over $3 or actions with high measured blast radius pause here.</p>
    {!state?.parked?.length ? <p className="empty ok">Nothing waiting. Ordinary work is proceeding autonomously — an empty queue is the product working.</p> : <ul className="timeline">{state.parked.map((p) => <GateItem key={String(p.id)} item={p} refresh={refresh} />)}</ul>}
  </section>;
}

export function FlagshipDashboard({ state, error, loading, refresh }: { state?: FlagshipState | null; error?: string | null; loading: boolean; refresh: () => Promise<unknown> | void }) {
  return <><MissionCard state={state} error={error} loading={loading} /><CycleCard state={state} /><LearningsCard state={state} /><GateCard state={state} refresh={refresh} /></>;
}
