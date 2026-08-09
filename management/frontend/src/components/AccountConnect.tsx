import { useState } from "react";
import { api } from "../api";
import { usePoll } from "../hooks";

export function AccountConnect() {
  const auth = usePoll(api.authStatus, 2000);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = auth.data;

  async function start() {
    setStarting(true);
    setError(null);
    try {
      await api.startDeviceAuth();
      await auth.refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="card account-connect" aria-live="polite">
      <h2>Connect your account</h2>
      {status?.connected ? (
        <p><span className="badge ok">Codex connected</span></p>
      ) : status?.pending ? (
        <div>
          <p>Open this URL on any device, sign in, and enter the one-time code:</p>
          <p><a href={status.verification_url ?? undefined} target="_blank" rel="noreferrer">
            {status.verification_url ?? "Requesting verification URL…"}
          </a></p>
          <p className="device-code" data-testid="device-code">{status.user_code ?? "Waiting for code…"}</p>
          <p className="muted small">This page polls until <code>codex login status</code> succeeds. No callback port is used.</p>
        </div>
      ) : (
        <div>
          <p className="muted">Connect a ChatGPT subscription before the loop can turn. No API key is requested.</p>
          <button type="button" onClick={() => void start()} disabled={starting}>
            {starting ? "Starting…" : "Connect your account"}
          </button>
        </div>
      )}
      {(error || status?.error || auth.error) && <p className="err">{error ?? status?.error ?? auth.error}</p>}
    </section>
  );
}
