import { useState } from "react";
import { api } from "../api";

// Cohort→main is manager_gated: a manager either approves or escalates to a human.
// This records the decision with its rationale (and optional uncertainty note, per the
// "document uncertainty in BB" doctrine).
export function ManagerMergeForm({ onDone }: { onDone?: () => void }) {
  const [decision, setDecision] = useState("approve");
  const [managerHandle, setManagerHandle] = useState("");
  const [cohortId, setCohortId] = useState("");
  const [rationale, setRationale] = useState("");
  const [uncertainty, setUncertainty] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const r = await api.managerMerge({
        decision,
        manager_handle: managerHandle,
        cohort_id: cohortId,
        rationale,
        uncertainty_note: uncertainty.trim() || null,
      });
      setResult(`recorded → ${JSON.stringify(r).slice(0, 120)}`);
      onDone?.();
    } catch (err) {
      setResult(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card gated">
      <h2>
        Manager merge decision <span className="chip">cohort → main</span>
      </h2>
      <p className="muted small">cohort → main is manager_gated: approve or escalate_human.</p>
      {decision === "approve" && (
        <p className="danger-banner" role="alert">
          Approve merges the cohort into main. Confirm the rationale before recording.
        </p>
      )}
      <form onSubmit={submit} className="form">
        <label>
          decision
          <select value={decision} onChange={(e) => setDecision(e.target.value)}>
            <option value="approve">approve</option>
            <option value="escalate_human">escalate_human</option>
          </select>
        </label>
        <label>
          manager_handle
          <input value={managerHandle} onChange={(e) => setManagerHandle(e.target.value)} required />
        </label>
        <label>
          cohort_id
          <input value={cohortId} onChange={(e) => setCohortId(e.target.value)} required />
        </label>
        <label>
          rationale
          <textarea value={rationale} onChange={(e) => setRationale(e.target.value)} required />
        </label>
        <label>
          uncertainty_note (optional)
          <textarea value={uncertainty} onChange={(e) => setUncertainty(e.target.value)} />
        </label>
        <button className="gated" disabled={busy} type="submit">
          {busy ? "recording…" : "Record decision"}
        </button>
      </form>
      {result && <p className="small mono">{result}</p>}
    </section>
  );
}
