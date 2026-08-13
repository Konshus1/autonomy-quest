"""Functional tests for the seeded agent's `aqmem` two-tier memory bridge (#4834).

The script is an executable named `aqmem` (no .py suffix), loaded by path. The database is faked
with a recording connection/cursor that models the two behaviours that MUST be correct, not merely
non-empty:

  * SEMANTIC: the fake computes cosine ordering from the query vector actually passed by `recall`
    against the vectors actually stored by `remember`. With a deterministic bag-of-words embedder,
    a semantically-similar query returns the RIGHT note first, and an unrelated query does NOT.
  * GRAPH: the fake models the world_model edges, so an added edge shows up in the neighborhood and
    an unrelated entity does not; a raw INSERT never happens for an injection-y label.

These assertions FAIL against a broken impl (wrong param wiring, no ORDER BY, unsanitized label),
which is the point (this repo's recurring bug class is "assert existence, not correctness").
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import math
import pathlib
import re

import pytest

_AQMEM_PATH = pathlib.Path(__file__).resolve().parents[1] / "agent_skills" / "memory" / "aqmem"


def _load_aqmem():
    spec = importlib.util.spec_from_file_location(
        "aqmem_mod", str(_AQMEM_PATH),
        loader=importlib.machinery.SourceFileLoader("aqmem_mod", str(_AQMEM_PATH)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aqmem = _load_aqmem()
_REAL_EMBED_TEXTS = aqmem.embed_texts  # captured before the autouse fixture stubs it


# ---------------------------------------------------------------------------
# Deterministic, network-free embedder (stand-in for the baked local model)
# ---------------------------------------------------------------------------
def _det_embed(text: str):
    """Bag-of-words hashed into EMBED_DIM, L2-normalized. Shared tokens -> high cosine similarity.

    Deterministic across processes (hashlib, not the salted builtin hash), so ordering is stable.
    """
    vec = [0.0] * aqmem.EMBED_DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16) % aqmem.EMBED_DIM
        vec[h] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


@pytest.fixture(autouse=True)
def deterministic_embedder(monkeypatch):
    """Every test uses the deterministic embedder unless it explicitly wants the model path."""
    monkeypatch.setattr(aqmem, "embed_text", lambda t: _det_embed(t))
    monkeypatch.setattr(aqmem, "embed_texts", lambda ts: [_det_embed(t) for t in ts])


def _cosine_distance(u, v):
    dot = sum(a * b for a, b in zip(u, v))
    return 1.0 - dot  # both L2-normalized


# ---------------------------------------------------------------------------
# Fake DB that models pgvector ordering AND the AGE world_model graph
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, params))
        self._rows = []
        self.description = None
        s = sql.lstrip()

        # Session/txn control statements are inert in the fake.
        if s.startswith(("SET ", "LOAD ", "SAVEPOINT", "ROLLBACK", "RELEASE", "DEALLOCATE")):
            return
        if "INSERT INTO agent_memory.notes" in sql:
            self.conn.note_seq += 1
            nid = self.conn.note_seq
            self.conn.notes.append({
                "id": nid,
                "instance_id": params["instance_id"],
                "kind": params["kind"],
                "text": params["text"],
                "embedding": json.loads(params["embedding"]),
                "metadata": params["metadata"],
                "created_at": "2026-08-13T00:00:00Z",
            })
            self._rows = [(nid, "2026-08-13T00:00:00Z")]
            return
        if "FROM agent_memory.notes" in sql and "ORDER BY" in sql:
            q = json.loads(params["q"])
            kind = params.get("kind")
            candidates = [n for n in self.conn.notes if kind is None or n["kind"] == kind]
            # HONOR the SQL's actual ORDER BY, not a re-derived one: parse the ordering EXPRESSION
            # and DIRECTION so a broken impl (DESC = farthest-first, or "ORDER BY id" = not by
            # distance) produces the wrong order here and the correctness assertions go RED.
            m = re.search(r"ORDER BY\s+(.*?)\s+LIMIT", sql, re.IGNORECASE | re.DOTALL)
            order_expr = m.group(1) if m else ""
            descending = re.search(r"\bDESC\b", order_expr, re.IGNORECASE) is not None
            if "<=>" in order_expr:  # ordered by cosine distance (the correct key)
                key = lambda n: _cosine_distance(q, n["embedding"])
            else:                    # ordered by something else (e.g. id) — model that faithfully
                key = lambda n: n["id"]
            scored = sorted(candidates, key=key, reverse=descending)
            top = scored[: params["k"]]
            self.description = [("id",), ("kind",), ("text",), ("metadata",),
                               ("created_at",), ("distance",)]
            self._rows = [
                (n["id"], n["kind"], n["text"], n["metadata"], n["created_at"],
                 _cosine_distance(q, n["embedding"]))
                for n in top
            ]
            return
        # World-model tier now uses AGE server-side parameter binding: PREPARE a statement whose
        # cypher() references $a/$b/$props/$name, then EXECUTE it with ONE agtype (JSON) argument.
        # The values therefore arrive via the EXECUTE param, NEVER interpolated into the query text.
        if s.startswith("PREPARE") and "cypher('world_model'" in sql:
            m = re.match(r"PREPARE\s+(\w+)\(", s)
            body = re.search(r"\$\$(.*?)\$\$", sql, re.DOTALL)
            if m and body:
                self.conn.prepared[m.group(1)] = body.group(1)
            return
        if s.startswith("EXECUTE"):
            m = re.match(r"EXECUTE\s+(\w+)\(", s)
            name = m.group(1) if m else None
            body = self.conn.prepared.get(name, "")
            # psycopg2 passes the single agtype JSON as the first positional param.
            payload = {}
            if params:
                payload = json.loads(params[0])
            if "MERGE" in body and "-[e:" in body:
                lm = re.search(r"-\[e:(\w+)\]->", body)
                label = lm.group(1) if lm else None
                self.conn.edges.append(
                    (payload.get("a"), label, payload.get("b"), payload.get("props")))
                return
            if "MATCH" in body:
                name_val = payload.get("name")
                if "type(e) AS edge" in body:
                    self.description = [("edge",), ("neighbor",)]
                    rows = []
                    for a, label, b, _p in self.conn.edges:
                        if a == name_val:
                            rows.append((label, b))
                        elif b == name_val:
                            rows.append((label, a))
                    self._rows = rows if rows else [(None, None)]
                else:
                    self.description = [("neighbor",)]
                    nbrs = set()
                    for a, _label, b, _p in self.conn.edges:
                        if a == name_val:
                            nbrs.add(b)
                        elif b == name_val:
                            nbrs.add(a)
                    self._rows = [(n,) for n in sorted(nbrs)] if nbrs else [(None,)]
                return
            return
        # Unknown statement — record only.

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.notes = []
        self.edges = []
        self.prepared = {}
        self.note_seq = 0
        self.readonly = None
        self.closed = False

    def set_session(self, readonly=False, autocommit=False):
        self.readonly = readonly

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        self.closed = True


@pytest.fixture
def db(monkeypatch):
    """A single shared fake connection so remember/relate persist across commands in a test."""
    conn = FakeConnection()
    monkeypatch.setenv("AQ_DB_URL", "postgres://aq_actor@localhost/aq")
    monkeypatch.setattr(aqmem.psycopg2, "connect", lambda dsn: conn)
    return conn


# ---------------------------------------------------------------------------
# DSN handling
# ---------------------------------------------------------------------------
def test_missing_dsn_fails_clearly(monkeypatch, capsys):
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    rc = aqmem.main(["recall", "anything"])
    assert rc == 2
    assert "AQ_DB_URL is not set" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# remember: parameterized, embedded, instance-stamped
# ---------------------------------------------------------------------------
def test_remember_parameterized_insert(db, monkeypatch, capsys):
    monkeypatch.setenv("AQ_INSTANCE_ID", "inst-7")
    rc = aqmem.main(["remember", "SMB buyers churn on slow onboarding",
                     "--kind", "learning", "--json"])
    assert rc == 0
    sql, params = [c for c in db.calls if "INSERT" in c[0]][-1]
    assert "%(embedding)s::vector" in sql and "%(metadata)s::jsonb" in sql
    # every value is a bound parameter — none inlined in the SQL text
    assert "SMB buyers" not in sql
    assert params["instance_id"] == "inst-7"
    assert params["kind"] == "learning"
    assert len(json.loads(params["embedding"])) == aqmem.EMBED_DIM
    assert '"note_id": 1' in capsys.readouterr().out


def test_remember_empty_text_rejected(db):
    assert aqmem.main(["remember", "   "]) == 2


def test_remember_bad_metadata_rejected(db):
    assert aqmem.main(["remember", "x", "--metadata", "not-json"]) == 2
    assert aqmem.main(["remember", "x", "--metadata", "[1,2]"]) == 2


# ---------------------------------------------------------------------------
# recall: returns the RIGHT note (content + ordering), rejects the unrelated one
# ---------------------------------------------------------------------------
NOTE_CANCEL = "how to cancel a customer subscription and process the refund"
NOTE_MARS = "the martian atmosphere is mostly carbon dioxide"
NOTE_GROWTH = "grow active paying customers by reducing onboarding friction"


def _seed_three(db):
    aqmem.main(["remember", NOTE_CANCEL])
    aqmem.main(["remember", NOTE_MARS])
    aqmem.main(["remember", NOTE_GROWTH])


def test_recall_returns_semantically_nearest(db, capsys):
    _seed_three(db)
    capsys.readouterr()
    rc = aqmem.main(["recall", "a customer wants to cancel their subscription", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows, "recall returned nothing"
    # THE crux: the nearest note is the cancellation note, not mars or growth.
    assert rows[0]["text"] == NOTE_CANCEL
    # ordered nearest-first by cosine distance (non-decreasing)
    dists = [r["distance"] for r in rows]
    assert dists == sorted(dists)
    assert db.readonly is True  # recall runs read-only


def test_recall_unrelated_query_does_not_return_the_note(db, capsys):
    _seed_three(db)
    capsys.readouterr()
    aqmem.main(["recall", "what is the martian atmosphere made of", "--json"])
    rows = json.loads(capsys.readouterr().out)
    # An unrelated query surfaces the mars note first, NOT the cancellation note.
    assert rows[0]["text"] == NOTE_MARS
    assert rows[0]["text"] != NOTE_CANCEL


def test_recall_k_limits_and_kind_filter(db, capsys):
    aqmem.main(["remember", NOTE_CANCEL, "--kind", "learning"])
    aqmem.main(["remember", NOTE_MARS, "--kind", "fact"])
    capsys.readouterr()
    aqmem.main(["recall", "cancel subscription", "--k", "1", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    capsys.readouterr()
    aqmem.main(["recall", "cancel subscription", "--kind", "fact", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert all(r["kind"] == "fact" for r in rows)
    assert all(r["text"] != NOTE_CANCEL for r in rows)  # learning note filtered out


def test_recall_query_vector_is_bound_not_inlined(db, capsys):
    _seed_three(db)
    aqmem.main(["recall", "cancel subscription", "--json"])
    sql, params = [c for c in db.calls if "ORDER BY" in c[0]][-1]
    assert "%(q)s::vector" in sql
    assert "ORDER BY embedding <=> %(q)s::vector" in sql
    assert isinstance(params["q"], str) and json.loads(params["q"])  # a bound vector literal


def test_recall_bad_k_rejected(db):
    assert aqmem.main(["recall", "x", "--k", "0"]) == 2


# ---------------------------------------------------------------------------
# Embedding dimension stability
# ---------------------------------------------------------------------------
def test_vector_literal_dimension_enforced():
    assert aqmem._vector_literal([0.0] * aqmem.EMBED_DIM).startswith("[")
    with pytest.raises(aqmem.AqmemError):
        aqmem._vector_literal([0.0] * (aqmem.EMBED_DIM - 1))


def test_embed_texts_rejects_wrong_dim(monkeypatch):
    # Restore the real embed_texts (autouse fixture stubbed it) and fake the MODEL underneath it.
    monkeypatch.setattr(aqmem, "embed_texts", _REAL_EMBED_TEXTS)

    class _BadModel:
        def embed(self, texts):
            return [[0.0] * (aqmem.EMBED_DIM + 1) for _ in texts]

    monkeypatch.setattr(aqmem, "_get_model", lambda: _BadModel())
    with pytest.raises(aqmem.AqmemError):
        aqmem.embed_texts(["x"])


def test_missing_model_dir_fails_clearly(monkeypatch, tmp_path):
    # Real model path, but the baked cache is absent -> clear error, NO network attempted.
    monkeypatch.setattr(aqmem, "_MODEL", None)
    monkeypatch.setattr(aqmem, "MODEL_CACHE_DIR", str(tmp_path / "absent"))
    with pytest.raises(aqmem.AqmemError) as exc:
        aqmem._get_model()
    assert "not present in the baked cache" in str(exc.value)


# ---------------------------------------------------------------------------
# Cypher label sanitization (the one non-parameterizable surface)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("good,expected", [
    ("OPERATES_IN", "OPERATES_IN"),
    ("competes with", "competes_with"),
    ("founded-by", "founded_by"),
    ("_private", "_private"),
])
def test_sanitize_label_accepts_identifiers(good, expected):
    assert aqmem.sanitize_edge_label(good) == expected


@pytest.mark.parametrize("bad", [
    "1STARTS_WITH_DIGIT",
    "has space and ]->() DETACH DELETE (n)",
    "label`with`backtick",
    "quote'injection",
    "semi;colon",
    "paren(s)",
    "arrow->there",
    "",
    "   ",
    "a" * 65,
])
def test_sanitize_label_rejects_injection(bad):
    with pytest.raises(aqmem.AqmemError):
        aqmem.sanitize_edge_label(bad)


# ---------------------------------------------------------------------------
# relate / world: correct edge shows up; unrelated does not; injection fails closed
# ---------------------------------------------------------------------------
def test_relate_parameterizes_values_and_substitutes_only_the_label(db, capsys):
    rc = aqmem.main(["relate", "Acme Co", "COMPETES_WITH", "Globex", "--json"])
    assert rc == 0
    # The only thing in the query TEXT is the validated label; it lands in the PREPAREd body.
    prepare_sql = [c[0] for c in db.calls if c[0].lstrip().startswith("PREPARE")][-1]
    assert "-[e:COMPETES_WITH]->" in prepare_sql
    assert "$a" in prepare_sql and "$b" in prepare_sql  # values referenced as bound cypher params
    # The values are NEVER interpolated into ANY SQL text the driver sends...
    for sql, _p in db.calls:
        assert "Acme Co" not in sql and "Globex" not in sql
    # ...they cross only via the EXECUTE agtype (JSON) parameter.
    exec_sql, exec_params = [c for c in db.calls if c[0].lstrip().startswith("EXECUTE")][-1]
    payload = json.loads(exec_params[0])
    assert payload["a"] == "Acme Co" and payload["b"] == "Globex"


def test_relate_backslash_injection_is_stored_as_a_literal_name(db, capsys):
    """RED-FIRST regression for the CRITICAL cypher-injection finding.

    The exploit `relate 'A\\' REL '}) WITH 1 AS _ MATCH (z:Entity) DETACH DELETE z //'` used a
    backslash-terminated name to escape the Cypher string literal and inject a DETACH DELETE. With
    server-side agtype binding the whole payload is DATA: it is stored as a literal entity name and
    never appears as executable query text. Against the old string-interpolating impl this FAILS
    (the payload lands in the query text and, live, wipes the graph).
    """
    payload_b = "}) WITH 1 AS _ MATCH (z:Entity) DETACH DELETE z //"
    rc = aqmem.main(["relate", "A\\", "REL", payload_b, "--json"])
    assert rc == 0
    # The dangerous payload never appears in ANY SQL text sent to the driver.
    for sql, _p in db.calls:
        assert payload_b not in sql
        assert "DETACH DELETE" not in sql
    # It is stored verbatim as the target entity name (data, not tokens).
    assert db.edges == [("A\\", "REL", payload_b, {})]


def test_relate_props_object_is_bound_not_inlined(db, capsys):
    rc = aqmem.main(["relate", "A", "REL", "B", "--props", '{"since": 2020}', "--json"])
    assert rc == 0
    exec_sql, exec_params = [c for c in db.calls if c[0].lstrip().startswith("EXECUTE")][-1]
    payload = json.loads(exec_params[0])
    assert payload["props"] == {"since": 2020}  # bound as a real map on the edge
    assert db.edges[-1][3] == {"since": 2020}


def test_world_unavailable_age_fails_clean_not_traceback(monkeypatch, capsys):
    """RED-FIRST for the HIGH crash finding: when age can't LOAD and cypher is unusable, relate/world
    must fail CLEAN (aqmem: <msg>, exit 2) — never leak an InFailedSqlTransaction traceback.

    Models the real prod session: `LOAD 'age'` raises (poisoning the txn unless SAVEPOINT-guarded)
    and the subsequent cypher PREPARE/EXECUTE raises a psycopg2 error (AGE genuinely unusable).
    """
    class _AgeBrokenCursor:
        def __init__(self):
            self.description = None
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, sql, params=None):
            s = sql.lstrip()
            if s.startswith("LOAD"):
                raise aqmem.psycopg2.Error('access to library "age" is not allowed')
            if s.startswith(("SET", "SAVEPOINT", "ROLLBACK", "RELEASE", "DEALLOCATE")):
                return
            if s.startswith(("PREPARE", "EXECUTE")):
                raise aqmem.psycopg2.Error("unhandled cypher(cstring) function call")
            return
        def fetchall(self):
            return []
        def fetchone(self):
            return None

    class _AgeBrokenConn:
        def set_session(self, readonly=False, autocommit=False):
            pass
        def cursor(self):
            return _AgeBrokenCursor()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def close(self):
            pass

    monkeypatch.setenv("AQ_DB_URL", "postgres://aq_actor@localhost/aq")
    monkeypatch.setattr(aqmem.psycopg2, "connect", lambda dsn: _AgeBrokenConn())
    rc = aqmem.main(["relate", "A", "REL", "B"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("aqmem:")
    assert "world-model tier unavailable" in err
    assert "Traceback" not in err
    # and `world` (read path) fails clean the same way
    rc2 = aqmem.main(["world", "A"])
    assert rc2 == 2
    assert "Traceback" not in capsys.readouterr().err


def test_world_neighborhood_is_correct(db, capsys):
    aqmem.main(["relate", "Acme Co", "COMPETES_WITH", "Globex"])
    aqmem.main(["relate", "Acme Co", "OPERATES_IN", "logistics"])
    aqmem.main(["relate", "Initech", "OPERATES_IN", "software"])  # unrelated to Acme
    capsys.readouterr()
    aqmem.main(["world", "Acme Co", "--json"])
    out = json.loads(capsys.readouterr().out)
    neighbors = {n["neighbor"] for n in out["neighbors"]}
    assert neighbors == {"Globex", "logistics"}  # correct set
    assert "software" not in neighbors and "Initech" not in neighbors  # unrelated excluded


def test_world_unknown_entity_has_no_neighbors(db, capsys):
    aqmem.main(["relate", "Acme Co", "COMPETES_WITH", "Globex"])
    capsys.readouterr()
    rc = aqmem.main(["world", "Nonexistent", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["neighbors"] == []


def test_relate_bad_label_fails_closed_no_mutation(db, capsys):
    rc = aqmem.main(["relate", "Acme", "DROP]->() DETACH DELETE (n) //", "Globex"])
    assert rc == 2
    assert "unsafe relationship label" in capsys.readouterr().err
    # NO cypher MERGE was ever issued — fail-closed before any DB mutation.
    assert not db.edges
    assert all("MERGE" not in c[0] for c in db.calls)


def test_relate_missing_entity_rejected(db):
    assert aqmem.main(["relate", "", "REL", "B"]) == 2
    assert aqmem.main(["relate", "A", "REL", "  "]) == 2


def test_world_depth_out_of_range_rejected(db):
    assert aqmem.main(["world", "Acme", "--depth", "0"]) == 2
    assert aqmem.main(["world", "Acme", "--depth", "9"]) == 2
