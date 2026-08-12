import { useState } from "react";
import { api } from "../api";
import type { ResultClaim } from "../api";
import { usePoll } from "../hooks";

// Replica-authored RESULTS view (#4834 comms Phase 2, design §9.4/§10 Phase 2).
//
// Every row is an UNTRUSTED CLAIM the replica authored and the host relayed into the parent journal.
// The panel badges it as a claim and shows the SEPARATE parent-owned verification state
// (unverified/verified/rejected). A claim is NEVER rendered as adopted or successful just because the
// replica said so: the operator independently verifies against ground truth, then records a verdict.
// Recording a verdict is NOT adoption/merge/promotion (a later, separately-gated action) — it only
// stamps whether the parent checked the claim. Operator-credentialed, same token UX as the fleet view.

const TOKEN_KEY = "aq.comms.operatorToken";

function verBadgeClass(state: string): string {
  if (state === "verified") return "ok";
  if (state === "rejected") return "err";
  return "warn"; // unverified — honestly amber, not neutral chrome
}

function ResultsTable({ token }: { token: string }) {
  const results = usePoll(() => api.results(token), 8000);
  const [busy, setBusy] = useState<string | null>(null);
  const items = results.data?.items;

  async function verify(id: string, state: "verified" | "rejected") {
    setBusy(id + state);
    try {
      await api.verifyResult(id, state, token);
    } catch {
      /* fail-safe UI: a verdict POST error just leaves state as-is; the poll re-syncs */
    } finally {
      setBusy(null);
    }
  }

  if (results.error) {
    const unauthorized = /-> 40[13]\b/.test(results.error);
    return (
      <p className="err">
        {unauthorized
          ? "operator credential rejected (401/403) — results are operator-only"
          : `error: ${results.error}`}
      </p>
    );
  }
  if (!items) return <p className="muted">{results.loading ? "loading…" : "no data"}</p>;
  if (items.length === 0) return <p className="muted">no replica-authored results yet</p>;

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="fleet-table" data-testid="results-table">
        <thead>
          <tr>
            <th>from / kind</th>
            <th>trust</th>
            <th>claimed</th>
            <th>summary</th>
            <th>artifacts (digests)</th>
            <th>verification</th>
            <th>actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r: ResultClaim) => (
            <tr key={r.id} data-testid="result-row">
              <td>
                <code className="small">{r.origin_instance_id ?? "—"}</code>
                <div>
                  <span className="badge">{r.kind}</span>
                </div>
              </td>
              <td>
                {/* ALWAYS a replica claim — never host-observed ground truth. */}
                <span className="badge warn">replica claim</span>
              </td>
              <td>{r.outcome_claimed ?? "—"}</td>
              <td>{r.summary ?? "—"}</td>
              <td>
                {r.artifact_refs.length === 0 ? (
                  <span className="muted">—</span>
                ) : (
                  <ul className="list small">
                    {r.artifact_refs.map((a, i) => (
                      <li key={i}>
                        {/* Digest only — never a clickable path/URL (SSRF boundary, design §8.5). */}
                        <code>{a.digest.slice(0, 22)}…</code>
                        {a.name ? <span className="muted"> {a.name}</span> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
              <td>
                <span className={`badge ${verBadgeClass(r.verification)}`}>{r.verification}</span>
                {r.verification_reason ? (
                  <div className="muted small">{r.verification_reason}</div>
                ) : null}
              </td>
              <td>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => verify(r.id, "verified")}
                >
                  verify
                </button>{" "}
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => verify(r.id, "rejected")}
                >
                  reject
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResultsPanel() {
  const [token] = useState<string>(() => localStorage.getItem(TOKEN_KEY) ?? "");

  return (
    <section className="card" aria-live="polite">
      <h2>Replica results (claims)</h2>
      <p className="muted small">
        Replica-authored <code>experiment.result</code> / <code>status.report</code> claims relayed
        into the parent journal. Every row is an <strong>untrusted claim</strong>; the parent
        independently verifies before anything is adopted. Recording a verdict here is not adoption.
      </p>
      {token ? (
        <ResultsTable token={token} />
      ) : (
        <p className="muted">
          Results are operator-only. Unlock the fleet panel above with the operator comms token to
          view and verify replica claims.
        </p>
      )}
    </section>
  );
}
