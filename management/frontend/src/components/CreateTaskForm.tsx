import { useState } from "react";
import { api } from "../api";

export function CreateTaskForm({ onDone }: { onDone?: () => void }) {
  const [title, setTitle] = useState("");
  const [workstreamId, setWorkstreamId] = useState("ws-default");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const r = await api.createTask({ title, workstream_id: workstreamId });
      setResult(`created → ${JSON.stringify(r).slice(0, 120)}`);
      setTitle("");
      onDone?.();
    } catch (err) {
      setResult(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Create task</h2>
      <form onSubmit={submit} className="form">
        <label>
          title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required minLength={1} />
        </label>
        <label>
          workstream_id
          <input value={workstreamId} onChange={(e) => setWorkstreamId(e.target.value)} />
        </label>
        <button disabled={busy} type="submit">
          {busy ? "creating…" : "Create"}
        </button>
      </form>
      {result && <p className="small mono">{result}</p>}
    </section>
  );
}
