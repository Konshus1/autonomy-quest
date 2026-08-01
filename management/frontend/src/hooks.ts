import { useCallback, useEffect, useRef, useState } from "react";

// Poll a fetcher on an interval. Honest loading/error states — a failed fetch surfaces the error,
// it does not silently render stale-as-fresh. Keeps the last good value while re-fetching.
export function usePoll<T>(fetcher: () => Promise<T>, intervalMs = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const tick = useCallback(async () => {
    try {
      const v = await fetcherRef.current();
      setData(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void tick();
    const id = setInterval(() => void tick(), intervalMs);
    return () => clearInterval(id);
  }, [tick, intervalMs]);

  return { data, error, loading, refresh: tick };
}
