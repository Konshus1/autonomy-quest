"""L1 regression: pending_reflection must see a PENDING acquisition, not only a running one.

Reads the executable SQL ONLY — the docstring above the query deliberately quotes the defective
pattern while explaining it, and an earlier version of this test matched that prose and failed on
correct code. A check that cannot tell code from a comment about code is not a check.
"""
import ast, pathlib

SRC = pathlib.Path("runner/db.py").read_text()


def _pending_reflection_sql() -> str:
    """Concatenate the string literals the function actually PASSES to _q, via the AST.

    ast.get_docstring is excluded by construction: we walk Call nodes, not the body prose.
    """
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "pending_reflection":
            parts = []
            for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
                for arg in call.args:
                    for lit in ast.walk(arg):
                        if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                            parts.append(lit.value)
            return " ".join(parts)
    raise AssertionError("pending_reflection not found")


def test_join_matches_pending_acquisitions():
    sql = _pending_reflection_sql().replace(" ", "")
    assert "plan_acquisition" in sql, "query no longer joins plan_acquisition"
    assert "pa.status='running'" not in sql, (
        "L1 REGRESSION: the join matches only 'running'. plan_acquisition.status DEFAULTS to "
        "'pending', so a freshly created acquisition is invisible, recovery tries work=done while "
        "an acquisition is open, and the DB refuses — the loop stops turning on restart."
    )
    assert "'pending'" in sql and "'running'" in sql, "join must match BOTH open states"


def test_terminal_states_are_not_treated_as_open():
    sql = _pending_reflection_sql()
    for terminal in ("'completed'", "'skipped'"):
        assert terminal not in sql, f"{terminal} is terminal and must not count as open"
