import { useState } from "react";
import { api } from "../api";
import type { FleetInstance, HealthState } from "../api";
import { usePoll } from "../hooks";

// The host-authoritative FLEET / TOPOLOGY view (#4834 comms Phase 1, design §9.2).
//
// Source: the host poller writes host_observed health.observed events into the parent journal; the
// operator-credentialed GET /api/fleet projects the latest per instance. This renders that as a
// typed table (NOT raw JSON): instance IDs/labels, project, lineage, workflow/SHA, host-observed
// health + last poll + heartbeat + live/stale/down, message counts, teardown/cap state, and a
// REDACTED host-local port (never a clickable arbitrary URL). Live via the existing poll.
//
// N3: the endpoint is operator-credentialed, so this panel needs the operator token. It is entered
// once and kept in localStorage (same UX register as the approval token) — with no token the panel
// is honestly LOCKED rather than showing an empty/fake fleet.

const TOKEN_KEY = "aq.comms.operatorToken";

function healthBadgeClass(state?: HealthState | null): string {
  // live -> ok (green), stale -> warn (amber), down -> err (red). Honest, not neutral chrome.
  if (state === "live") return "ok";
  if (state === "stale") return "warn";
  if (state === "down") return "err";
  return "warn";
}

function shortSha(sha?: string | null): string {
  return sha ? String(sha).slice(0, 12) : "—";
}

function FleetTable({ token }: { token: string }) {
  const fleet = usePoll(() => api.fleet(token), 8000);
  const items = fleet.data?.items;

  if (fleet.error) {
    const unauthorized = /-> 40[13]\b/.test(fleet.error);
    return (
      <p className="err">
        {unauthorized
          ? "operator credential rejected (401/403) — the fleet view is operator-only"
          : `error: ${fleet.error}`}
      </p>
    );
  }
  if (!items) return <p className="muted">{fleet.loading ? "loading…" : "no data"}</p>;
  if (items.length === 0) return <p className="muted">no replicas observed yet</p>;

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="fleet-table" data-testid="fleet-table">
        <thead>
          <tr>
            <th>instance / project</th>
            <th>health</th>
            <th>SHA</th>
            <th>heartbeat</th>
            <th>workflow</th>
            <th>mgmt port</th>
            <th>msgs</th>
            <th>cap</th>
            <th>last poll</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it: FleetInstance) => {
            const mismatch =
              it.observed_git_sha &&
              it.expected_git_sha &&
              it.observed_git_sha !== it.expected_git_sha;
            return (
              <tr key={it.instance_id} data-testid={`fleet-row-${it.instance_id}`}>
                <td>
                  <strong>{it.instance_id}</strong>
                  <span className="muted"> · {it.project ?? "—"}</span>
                  {it.torn_down && <span className="badge warn"> torn down</span>}
                </td>
                <td>
                  <span className={`badge ${healthBadgeClass(it.health_state)}`} data-testid={`health-${it.instance_id}`}>
                    {it.health_state ?? "unknown"}
                  </span>
                  {it.reason && <div className="muted small">{it.reason}</div>}
                </td>
                <td>
                  <code>{shortSha(it.git_sha)}</code>
                  {mismatch && (
                    <div className="badge err small">version mismatch</div>
                  )}
                </td>
                <td>
                  {it.cycling == null ? (
                    <span className="muted">—</span>
                  ) : (
                    <span className={`badge ${it.cycling ? "ok" : "warn"}`}>
                      {it.cycling ? "cycling" : "idle"}
                    </span>
                  )}
                </td>
                <td>
                  {it.workflow ?? "—"}
                  {it.workflow_version ? <span className="muted"> {it.workflow_version}</span> : null}
                </td>
                {/* Redacted host-local port — plain text, never a clickable link (design §9.2). */}
                <td><code>{it.endpoint_redacted ?? "—"}</code></td>
                <td>{it.message_count}</td>
                <td>
                  <span className={it.counts_against_cap ? "warn" : "muted"}>
                    {it.counts_against_cap ? "counts" : "released"}
                  </span>
                </td>
                <td className="muted small">{it.observed_at ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function FleetPanel() {
  const [token, setToken] = useState<string>(() => localStorage.getItem(TOKEN_KEY) ?? "");
  const [draft, setDraft] = useState<string>("");

  function save() {
    const t = draft.trim();
    localStorage.setItem(TOKEN_KEY, t);
    setToken(t);
    setDraft("");
  }
  function clear() {
    localStorage.removeItem(TOKEN_KEY);
    setToken("");
  }

  return (
    <section className="card" aria-live="polite">
      <h2>Fleet / topology</h2>
      <p className="muted small">
        Host-observed replica health from the parent journal (host_observed events — the observer,
        not a replica claim). Operator-credentialed.
      </p>
      {token ? (
        <>
          <FleetTable token={token} />
          <p className="muted small">
            operator credential loaded ·{" "}
            <button type="button" className="linklike" onClick={clear}>
              clear
            </button>
          </p>
        </>
      ) : (
        <div className="kv">
          <p className="muted">
            The fleet view is not world-readable on the mgmt port. Enter the operator comms token to
            view topology + host-observed health.
          </p>
          <div>
            <input
              type="password"
              aria-label="operator comms token"
              placeholder="operator comms token"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <button type="button" onClick={save} disabled={!draft.trim()}>
              Unlock fleet
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
