import type { ReactNode } from "react";

// Generic list card with honest empty/error/loading states.
export function ListPanel<T>({
  title,
  items,
  error,
  loading,
  render,
}: {
  title: string;
  items: T[] | undefined;
  error: string | null;
  loading: boolean;
  render: (item: T) => ReactNode;
}) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {error ? (
        <p className="err">error: {error}</p>
      ) : !items ? (
        <p className="muted">{loading ? "loading…" : "no data"}</p>
      ) : items.length === 0 ? (
        <p className="muted">none yet</p>
      ) : (
        <ul className="list">
          {items.map((it, i) => (
            <li key={i}>{render(it)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
