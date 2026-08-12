import { useState } from "react";
import { api } from "../api";
import type { InboundWork } from "../api";
import { usePoll } from "../hooks";

// Inbound work-request inbox (#4834 comms Phase 3, design §9.1/§9.3). The FIRST parent->replica command
// direction, shown as UNTRUSTED PROPOSALS — never as authorized or done work. Each row is badged
// "untrusted proposal" and carries its parent-owned import state (default not_imported, since the
// importer is default-off). When imported it shows "requires approval", so the operator sees an
// imported request is STILL gated, not executed. Keep conversation separate from authority controls:
// this panel never offers a one-click "do it" — import/approval happens only via the existing gated
// flow, never from a message card (design §9.3). Operator-credentialed, same token UX as the fleet view.

const TOKEN_KEY = "aq.comms.operatorToken";

function importBadgeClass(w: InboundWork): string {
  if (w.import_state === "rejected") return "err";
  if (w.import_state === "imported") return w.requires_human ? "warn" : "ok";
  return ""; // not_imported — neutral (inert)
}

function importLabel(w: InboundWork): string {
  if (w.import_state === "rejected") return "rejected";
  if (w.import_state === "imported") return w.requires_human ? "imported · requires approval" : "imported";
  return "not imported";
}

function InboxList({ token }: { token: string }) {
  const inbox = usePoll(() => api.inbox(token), 8000);
  const items = inbox.data?.items;

  if (inbox.error) {
    const unauthorized = /-> 40[13]\b/.test(inbox.error);
    return (
      <p className="err">
        {unauthorized
          ? "operator credential rejected (401/403) — the inbox is operator-only"
          : `error: ${inbox.error}`}
      </p>
    );
  }
  if (!items) return <p className="muted">{inbox.loading ? "loading…" : "no data"}</p>;
  if (items.length === 0) return <p className="muted">no inbound work requests</p>;

  return (
    <ul className="list">
      {items.map((w: InboundWork, i: number) => (
        <li key={w.id ?? i} data-testid="inbox-row">
          <span className="badge">{w.kind}</span>{" "}
          <span className="badge warn" title="A work.request is untrusted input, not authority.">
            untrusted proposal
          </span>{" "}
          <span
            className={`badge ${importBadgeClass(w)}`}
            title="Storage granted no capability; import runs every local gate."
          >
            {importLabel(w)}
          </span>{" "}
          <strong>{w.origin_instance_id ?? "—"}</strong>
          <span className="muted"> → {w.target_instance_id ?? "—"}</span>{" "}
          {w.priority ? <span className="badge">priority: {w.priority}</span> : null}
          {w.cancel_of ? (
            <span className="badge" title="Cancellation as a request, never a process signal.">
              cancel-of {w.cancel_of}
            </span>
          ) : null}
          {w.goal ? <span> · {w.goal}</span> : null}
          {w.state ? <span className="muted"> · state: {w.state}</span> : null}
          {w.disposition ? <span className="muted"> · {w.disposition}</span> : null}
          {w.gate_reason ? <span className="muted small"> · gate: {w.gate_reason}</span> : null}
          {w.created_at ? <span className="muted small"> · {w.created_at}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function InboxPanel() {
  const [token] = useState<string>(() => localStorage.getItem(TOKEN_KEY) ?? "");

  return (
    <section className="card" aria-live="polite">
      <h2>Inbound work requests</h2>
      <p className="muted small">
        Parent/operator <code>work.request</code> proposals delivered to a replica inbox. Every row is{" "}
        <strong>untrusted input</strong>, stored inert (granting no capability). It is imported only
        behind the default-off importer, and even then enters the local queue subject to every budget,
        blast-radius, and approval gate. A message can never actuate, replicate, or self-approve.
      </p>
      {token ? (
        <InboxList token={token} />
      ) : (
        <p className="muted">
          The inbox is operator-only. Unlock the fleet panel above with the operator comms token to
          view inbound work requests.
        </p>
      )}
    </section>
  );
}
