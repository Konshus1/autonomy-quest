-- Required database capabilities for every fresh Autonomy Quest instance.

-- pgvector is genuinely required: embedding columns are declared with its type, so a database
-- without it cannot hold the schema at all. Fail loudly here rather than midway through 001.
CREATE EXTENSION IF NOT EXISTS vector;

-- APACHE AGE IS OPTIONAL. Install it ONLY if the server actually offers it.
--
-- This file used to say `CREATE EXTENSION IF NOT EXISTS age;` unconditionally, which made AGE a
-- hard requirement for applying ANY of this schema. That was wrong on the facts: AGE is not
-- semantically load-bearing (BB #2617). The causal, planning and governance substrate is ordinary
-- PostgreSQL — tables, JSONB, arrays, indexes, PL/pgSQL. AGE supplies one optional mirror graph of
-- Work/Run/Learning rows that the application writes only when `datastore.graph == "age"` and
-- NEVER READS BACK. There is exactly one executable Cypher path in the repository
-- (runner/db.py::graph_link), and it returns immediately for every mode except `age`.
--
-- The cost of the old line was concrete: on stock PostgreSQL, `scripts/apply_migrations.py` died
-- with `could not open extension control file ".../age.control"`, so NOBODY could apply the schema
-- outside the Docker image — not a contributor cloning the public repo, not a local test run.
-- `instance.yaml` has shipped `graph: none` the whole time. A dependency that no configured code
-- path uses was blocking every configured code path that does.
--
-- `IF NOT EXISTS` alone does not help: it suppresses "already exists", not "not installed".
-- pg_available_extensions is the right test — it lists what the SERVER could install, which is
-- exactly the question, rather than what happens to be installed already.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'age') THEN
    CREATE EXTENSION IF NOT EXISTS age;
    RAISE NOTICE 'AQ: Apache AGE available — graph mirror enabled if datastore.graph=age';
  ELSE
    -- NOT silent. A skip nobody can see is how "optional" turns back into "mysteriously missing".
    RAISE NOTICE 'AQ: Apache AGE not available on this server — continuing WITHOUT the graph mirror. This is supported: datastore.graph must be "none" (the default). Set graph=age only on a server that offers the extension.';
  END IF;
END
$$;
