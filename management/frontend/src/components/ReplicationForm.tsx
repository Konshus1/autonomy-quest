import { useState } from "react";
import { api } from "../api";

// Guest PROPOSES a replication (clone/copy). The host executes it, and it is operator-gated
// unless AQ_REPLICATION_AUTO_APPROVE is set on the host. This form only proposes — it never
// executes; the server returns the gated status.
export function ReplicationForm({
  overrideEnv,
  overrideEnabled,
  onDone,
}: {
  overrideEnv?: string;
  overrideEnabled?: boolean;
  onDone?: () => void;
}) {
  const [mode, setMode] = useState("host_copy");
  const [missionId, setMissionId] = useState("");
  const [requester, setRequester] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const r = await api.proposeReplication({
        mode,
        mission_id: missionId,
        requester_instance_id: requester,
      });
      setResult(`proposed → status: ${String(r.status ?? "?")}`);
      onDone?.();
    } catch (err) {
      setResult(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Propose replication</h2>
      <p className="muted small">
        Guest proposes; host executes. {overrideEnv ?? "AQ_REPLICATION_AUTO_APPROVE"} ={" "}
        <span className={overrideEnabled ? "err" : "ok"}>
          {overrideEnabled ? "ON (auto-approve)" : "off (operator-gated)"}
        </span>
      </p>
      <form onSubmit={submit} className="form">
        <label>
          mode
          <input value={mode} onChange={(e) => setMode(e.target.value)} required />
        </label>
        <label>
          mission_id
          <input value={missionId} onChange={(e) => setMissionId(e.target.value)} required />
        </label>
        <label>
          requester_instance_id
          <input value={requester} onChange={(e) => setRequester(e.target.value)} required />
        </label>
        <button disabled={busy} type="submit">
          {busy ? "proposing…" : "Propose"}
        </button>
      </form>
      {result && <p className="small mono">{result}</p>}
    </section>
  );
}
