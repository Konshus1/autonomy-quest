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

  // F1: three states, not two. When the override state has not loaded (undefined), we must
  // NOT render a safe-green "operator-gated" — that asserts a posture we don't know.
  const overrideKnown = typeof overrideEnabled === "boolean";
  const envName = overrideEnv ?? "AQ_REPLICATION_AUTO_APPROVE";

  return (
    <section className="card gated">
      <h2>
        Propose replication <span className="chip">host-gated</span>
      </h2>
      {overrideEnabled === true && (
        <p className="danger-banner" role="alert">
          {envName}=ON — replication AUTO-APPROVES on the host (operator gate bypassed).
        </p>
      )}
      <p className="muted small">
        Guest proposes; host executes. {envName} ={" "}
        {!overrideKnown ? (
          <span className="unknown">unknown — state not loaded</span>
        ) : overrideEnabled ? (
          <span className="err">ON (auto-approve)</span>
        ) : (
          <span className="ok">off (operator-gated)</span>
        )}
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
        <button className="gated" disabled={busy} type="submit">
          {busy ? "proposing…" : "Propose (host-gated)"}
        </button>
      </form>
      {result && <p className="small mono">{result}</p>}
    </section>
  );
}
