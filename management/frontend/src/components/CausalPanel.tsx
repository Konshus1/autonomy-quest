import { useState } from "react";
import { api, type CausalEdge, type CausalEvidence } from "../api";
import { usePoll } from "../hooks";

// Read-only viewer for mined causal principles (BB #764, roadmap surfaced live).
// Each edge is a FUZZY guiding principle mined from mission-loop outcomes, with provenance
// (which run produced it). The surprise loop ACCRUES support toward promotion, but no promotion
// is actuated in the live system — every edge stays fuzzy; nothing is a guaranteed rule yet.

// The certainty an evidence entry predicted for its run. Producers vary between the
// `predicted` and `predicted_certainty` keys (see CausalEvidence); read whichever is set.
function predictedOf(ev: CausalEvidence): number | null {
  const p = ev.predicted ?? ev.predicted_certainty;
  return typeof p === "number" ? p : null;
}

// Whether the causal claim HELD on that run: actual >= 0.5 is a confirming observation
// (ralph_portable.causal_edges.surprise). Unknown when `actual` is absent.
function heldOf(ev: CausalEvidence): boolean | null {
  return typeof ev.actual === "number" ? ev.actual >= 0.5 : null;
}

// A tiny read-only sparkline of EARNED SUPPORT accumulating over the evidence sequence
// (support only advances on a `confirm` — the same gate as support_count). It explains, at a
// glance, how the "earned support N" number was reached over time. Nothing here is writable.
function SupportSparkline({ evidence }: { evidence: CausalEvidence[] }) {
  const w = 84;
  const h = 18;
  let running = 0;
  const cumulative = evidence.map((ev) => {
    if (ev.signal === "confirm") running += 1;
    return running;
  });
  const max = Math.max(1, running);
  const n = cumulative.length;
  const points = cumulative
    .map((v, i) => {
      const x = n === 1 ? 0 : (i / (n - 1)) * (w - 2) + 1;
      const y = h - 1 - (v / max) * (h - 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      className="spark"
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`earned support rose to ${running} over ${n} observation(s)`}
    >
      <polyline
        points={points}
        fill="none"
        stroke="#6ea8fe"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function EvidenceHistory({ evidence }: { evidence: CausalEvidence[] }) {
  return (
    <div className="evidence">
      <SupportSparkline evidence={evidence} />
      <ul className="ev-list">
        {evidence.map((ev, i) => {
          const p = predictedOf(ev);
          const held = heldOf(ev);
          const outcome = held === null ? "outcome unknown" : held ? "succeeded" : "failed";
          const cls = held === null ? "" : held ? "ok" : "warn";
          return (
            <li key={i} className="ev-row mono small">
              <span className="muted">predicted {p === null ? "?" : p.toFixed(2)}</span>{" "}
              <span aria-hidden="true">→</span>{" "}
              <span className={cls}>{outcome}</span>
              {ev.signal && <span className="muted"> ({String(ev.signal)})</span>}
              {typeof ev.surprise === "number" && (
                <span className="muted"> · surprise {ev.surprise.toFixed(2)}</span>
              )}
            </li>
          );
        })}
      </ul>
      <p className="muted small">
        Support only advances on a <code>confirm</code>; this history is read-only evidence, not a
        rule.
      </p>
    </div>
  );
}

export function CausalPanel() {
  const edges = usePoll(api.causalEdges, 8000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  function toggle(i: number) {
    setExpanded((prev) => ({ ...prev, [i]: !prev[i] }));
  }

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
        Fuzzy causal principles mined from real mission-loop outcomes. The surprise loop <b>accrues
        support</b> toward promotion, but promotion is not yet applied — every edge here is fuzzy,
        nothing is a guaranteed rule (formalization is roadmap).
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
          {items.map((e, i) => {
            const evidence = Array.isArray(e.evidence) ? e.evidence : [];
            const support = e.support_count ?? 0;
            const isOpen = !!expanded[i];
            return (
              <li key={i}>
                <strong>
                  {String(e.cause)} → {String(e.effect)}
                </strong>{" "}
                <span className={`badge ${badgeClass(e.formality)}`}>{e.formality}</span>{" "}
                <span className="badge">{e.directness}</span>{" "}
                <span className="muted small">
                  observed in {e.observed_runs ?? (e.provenance?.length ?? 0)} run(s)
                </span>
                {support > 0 &&
                  (evidence.length > 0 ? (
                    <button
                      type="button"
                      className="ev-toggle small"
                      onClick={() => toggle(i)}
                      aria-expanded={isOpen}
                    >
                      earned support {support} {isOpen ? "▾" : "▸"}
                    </button>
                  ) : (
                    <span className="muted small"> · earned support {support}</span>
                  ))}
                {Array.isArray(e.provenance) && e.provenance[0] && (
                  <div className="muted small">
                    from run {e.provenance.map((p) => String(p.run_id)).join(", ")}:{" "}
                    {String(e.provenance[0].insight ?? "").slice(0, 90)}
                  </div>
                )}
                {isOpen && evidence.length > 0 && <EvidenceHistory evidence={evidence} />}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
