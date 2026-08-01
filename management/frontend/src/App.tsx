import { api } from "./api";
import { usePoll } from "./hooks";
import { StatePanel } from "./components/StatePanel";
import { ListPanel } from "./components/ListPanel";
import { ReplicationForm } from "./components/ReplicationForm";
import { ManagerMergeForm } from "./components/ManagerMergeForm";
import { CreateTaskForm } from "./components/CreateTaskForm";
import { CausalPanel } from "./components/CausalPanel";

export function App() {
  const health = usePoll(api.health, 10000);
  const state = usePoll(api.state, 5000);
  const workstreams = usePoll(api.workstreams, 8000);
  const tasks = usePoll(api.tasks, 8000);
  const comms = usePoll(api.agentComms, 8000);

  const healthy = health.data && (health.data.ok ?? health.data.status === "ok");

  return (
    <div className="app">
      <header>
        <h1>AQ Ralph Control — Management</h1>
        <span className="badge">Task #4407</span>
        <span className="badge">React shell</span>
        <span className={`badge ${healthy ? "ok" : health.error ? "err" : "warn"}`}>
          health: {health.error ? "unreachable" : healthy ? "ok" : "…"}
        </span>
      </header>

      <main>
        <StatePanel state={state.data} error={state.error} loading={state.loading} />

        <ListPanel
          title="Workstreams"
          error={workstreams.error}
          loading={workstreams.loading}
          items={workstreams.data?.items}
          render={(w) => (
            <>
              <strong>{String(w.title ?? w.id)}</strong>
              <span className="muted"> · {String(w.status ?? "—")}</span>
            </>
          )}
        />

        <ListPanel
          title="Tasks"
          error={tasks.error}
          loading={tasks.loading}
          items={tasks.data?.items}
          render={(t) => (
            <>
              <strong>{String(t.title)}</strong>
              <span className="muted"> · {String(t.workstream_id ?? "ws-default")}</span>
            </>
          )}
        />

        <ListPanel
          title="Agent comms"
          error={comms.error}
          loading={comms.loading}
          items={comms.data?.items}
          render={(c) => <code>{JSON.stringify(c)}</code>}
        />

        <ReplicationForm
          overrideEnv={state.data?.replication.override_env}
          overrideEnabled={state.data?.replication.override_enabled}
          onDone={() => void state.refresh()}
        />

        <ManagerMergeForm onDone={() => void state.refresh()} />

        <CreateTaskForm onDone={() => void tasks.refresh()} />

        <CausalPanel />
      </main>

      <footer>
        React shell served by the FastAPI management API. Every write is gated server-side:
        cohort→main is <code>manager_gated</code>; replication is operator-gated unless the host sets{" "}
        <code>AQ_REPLICATION_AUTO_APPROVE</code>. Interim vanilla console stays as the no-build fallback.
        {" · "}
        <a href="/docs">/docs</a> · <a href="/health">/health</a>
      </footer>
    </div>
  );
}
