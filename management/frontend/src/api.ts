// Typed client for the FastAPI management API (management/api/app.py).
// Types mirror the ACTUAL response/request shapes under the #4407 contract lock:
// response shapes are stable + additive-only; `backing` flips "in_memory_stub" -> "postgres"
// when v6 lands, so nothing here branches on that literal — it is rendered, not switched on.

export interface RalphState {
  ok: boolean;
  backing: string; // "in_memory_stub" | "postgres" (rendered as a badge, never branched on)
  merge_gate: string; // "manager_gated"
  replication: {
    override_env: string; // AQ_REPLICATION_AUTO_APPROVE
    override_enabled: boolean;
    pending: number;
    total: number;
  };
  counts: {
    workstreams: number;
    tasks: number;
    agent_comms: number;
    merge_decisions: number;
  };
}

export interface Health {
  ok?: boolean;
  status?: string;
  merge_gate?: string;
  [k: string]: unknown;
}

export interface Workstream {
  id: string;
  title: string;
  status: string;
  [k: string]: unknown;
}
export interface Task {
  id?: string;
  title: string;
  workstream_id?: string;
  [k: string]: unknown;
}
export interface AgentComm {
  [k: string]: unknown;
}

// POST bodies — identical shapes the API validates (ReplicationIn / ManagerMergeIn / TaskIn).
export interface ReplicationIn {
  mode: string;
  mission_id: string;
  requester_instance_id: string;
}
export interface ManagerMergeIn {
  decision: string;
  manager_handle: string;
  cohort_id: string;
  rationale: string;
  uncertainty_note?: string | null;
}
export interface TaskIn {
  title: string;
  workstream_id?: string;
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { accept: "application/json" } });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return (await r.json()) as T;
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = "";
    try {
      detail = JSON.stringify((await r.json()).detail ?? "");
    } catch {
      /* ignore */
    }
    throw new Error(`${url} -> ${r.status} ${detail}`);
  }
  return (await r.json()) as T;
}

export const api = {
  state: () => getJSON<RalphState>("/api/ralph/state"),
  health: () => getJSON<Health>("/health"),
  workstreams: () => getJSON<{ items: Workstream[] }>("/api/workstreams"),
  tasks: () => getJSON<{ items: Task[] }>("/api/tasks"),
  agentComms: () => getJSON<{ items: AgentComm[] }>("/api/agent-comms"),
  proposeReplication: (b: ReplicationIn) =>
    postJSON<Record<string, unknown>>("/api/replication/propose", b),
  managerMerge: (b: ManagerMergeIn) =>
    postJSON<Record<string, unknown>>("/api/manager/merge-decision", b),
  createTask: (b: TaskIn) => postJSON<Record<string, unknown>>("/api/tasks", b),
  causalEdges: () => getJSON<{ items: CausalEdge[] }>("/api/causal/edges"),
  mineCausal: () => postJSON<{ mined?: number }>("/api/causal/mine", {}),
};

// A single surprise result recorded as evidence on an edge (ralph_portable.causal_edges.surprise
// / causal_edge_store.record_evidence). Read-only history: the certainty predicted for a run vs the
// actual outcome, plus the GATED learning signal it implied. Fields are optional/loosely typed
// because the store is additive — older entries may omit fields, and producers vary between the
// `predicted` and `predicted_certainty` keys for the same value.
export interface CausalEvidence {
  predicted_certainty?: number;
  predicted?: number;
  actual?: number; // [0,1]; >= 0.5 means the causal claim held (a confirming observation)
  signal?: string; // "confirm" | "demote" | "investigate"
  surprise?: number; // |predicted - actual|
  [k: string]: unknown;
}

export interface CausalEdge {
  cause: string;
  effect: string;
  formality?: string;
  strictness?: string;
  directness?: string;
  support_count?: number;
  observed_runs?: number;
  evidence?: CausalEvidence[];
  evidence_run_ids?: unknown[];
  provenance?: { run_id?: unknown; learning_id?: unknown; insight?: string }[];
  [k: string]: unknown;
}
