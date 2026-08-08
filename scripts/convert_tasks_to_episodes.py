#!/usr/bin/env python3
"""Convert completed Ralph tasks into episodes for T3 mining + T11 frame expansion.

Extracts real, diverse work from the task database — 2,386 completed tasks across
19 areas (betterself, mobile, reliability_ralph, ui, web, backend, deployment, etc.).
Each task becomes an episode with attributes extracted from its title, area, and
details (summary, outcome, work_kind). This is the diversity source that T10 needs
to find cross-domain conflicts and T11 needs to find real frame gaps.

Usage:
    PYTHONPATH=. python3.11 scripts/convert_tasks_to_episodes.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

# Stop words for attribute extraction
STOP_WORDS = frozenset("""
the this that what when which there their they have has had been were was will would
could should about into only each very just your being must some these those other
whose from with more most than then also first second third before after every
always never prefer validate verify ensure using based declaring complete functionality
through multi task work item status area details parent child summary outcome
""".split())


def extract_attributes(title: str, area: str, details: dict) -> list[str]:
    """Extract concept-level attributes from a task for frame-expansion mapping.

    Pulls from lightweight semantic content (title, summary, area), NOT the massive
    completion_packet / closeout JSON that some tasks carry.
    """
    attrs: list[str] = []
    seen: set[str] = set()

    def add(attr: str) -> None:
        attr = attr.lower().strip().replace("-", "_").replace(" ", "_")
        if attr and len(attr) >= 4 and attr not in seen and attr not in STOP_WORDS:
            attrs.append(attr)
            seen.add(attr)

    # Area as primary attribute
    if area:
        add(area)

    # Work kind if present
    wk = details.get("work_kind", "")
    if wk and isinstance(wk, str):
        add(wk)

    # Key words from title (this is the richest lightweight source)
    if title and isinstance(title, str):
        words = re.findall(r"[a-z]{5,}", title.lower())
        for w in words:
            add(w)
            if len(attrs) >= 6:
                break

    # Key concepts from summary only (NOT from completion_packet/closeout/verifier_plan)
    summary = details.get("summary", "")
    if isinstance(summary, str) and len(summary) > 10 and len(summary) < 500:
        words = re.findall(r"[a-z]{5,}", summary.lower())
        for w in words[:8]:
            add(w)
            if len(attrs) >= 6:
                break

    # Lightweight classification fields
    for field in ("component", "tool_name", "type", "epic", "repo_affinity"):
        val = details.get(field, "")
        if isinstance(val, str) and val and len(val) < 100:
            add(val)

    return attrs[:8]  # cap at 8 attributes


def convert_tasks_to_episodes(limit: int = 200) -> list[dict]:
    """Convert completed tasks from the DB into episodes for T11/T3.

    Returns a list of episode dicts: {episode_id, attributes, relational_graph}.
    """
    conn = psycopg2.connect(host="localhost", port=5432, dbname="postgres", user="kevincthomas")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title,
               COALESCE(details->>'area', 'unknown') as area,
               COALESCE(details->>'summary', '') as summary,
               COALESCE(details->>'work_kind', '') as work_kind,
               COALESCE(details->>'component', '') as component,
               COALESCE(details->>'tool_name', '') as tool_name,
               COALESCE(details->>'type', '') as type,
               COALESCE(details->>'repo_affinity', '') as repo_affinity
        FROM task
        WHERE status = 'completed'
          AND details IS NOT NULL
        ORDER BY id DESC
        LIMIT %s
    """, (limit,))

    episodes = []
    for row in cur.fetchall():
        task_id = row[0]
        title = row[1] or ""
        area = row[2] or "unknown"
        # Build a lightweight details dict from the extracted fields
        details = {
            "summary": row[3] or "",
            "work_kind": row[4] or "",
            "component": row[5] or "",
            "tool_name": row[6] or "",
            "type": row[7] or "",
            "repo_affinity": row[8] or "",
        }

        attrs = extract_attributes(title, area, details)
        if len(attrs) >= 2:
            episodes.append({
                "episode_id": f"task_{task_id}",
                "attributes": attrs,
                "relational_graph": {
                    "nodes": [
                        {"id": area, "type": "domain"},
                        {"id": str(task_id), "type": "task"},
                    ],
                    "edges": [
                        {"src": str(task_id), "dst": area, "relation": "belongs_to"},
                    ],
                },
                "source": {
                    "task_id": task_id,
                    "title": title[:100],
                    "area": area,
                },
            })

    conn.close()
    return episodes


def run_t10_on_real_principles() -> dict:
    """Run T10 on the real causal_principle table."""
    from ralph_portable.inconsistency_detector import scan_inconsistencies

    conn = psycopg2.connect(host="localhost", port=5432, dbname="postgres", user="kevincthomas")
    cur = conn.cursor()
    cur.execute("""
        SELECT principle_id, principle_text, tags, status,
               failure_modes_addressed, formalization_hint
        FROM causal_principle WHERE is_active = true
    """)

    principles = []
    for row in cur.fetchall():
        principles.append({
            "principle_id": row[0],
            "principle_text": row[1] or "",
            "tags": row[2] if row[2] else [],
            "status": row[3] or "provisional",
            "failure_modes_addressed": row[4] if row[4] else [],
            "formalization_hint": row[5] or "",
            "is_active": True,
        })
    conn.close()

    return scan_inconsistencies(principles)


def run_t3_mining_on_task_episodes(episodes: list[dict]) -> list[dict]:
    """Run T3 mining on the task episodes to produce diverse causal edges."""
    from ralph_portable.principle_mining import mine_causal_edges

    # Convert episodes to the observation format the miner expects
    obs = []
    for ep in episodes:
        source = ep.get("source", {})
        attrs = ep.get("attributes", [])
        obs.append({
            "work_kind": attrs[0] if attrs else "unknown",
            "succeeded": True,
            "measure_before": 0.0,
            "measure_after": 1.0,
            "learning_insight": source.get("title", ""),
            "learning_confidence": 0.6,
            "run_id": source.get("task_id", 0),
            "work_id": source.get("task_id", 0),
            "learning_id": source.get("task_id", 0),
        })

    return mine_causal_edges(obs)


def main() -> None:
    print("=" * 60)
    print("TASK-DB TO EPISODES CONVERTER + T10/T11/T3 ON DIVERSE REAL DATA")
    print("=" * 60)

    # Step 1: Convert tasks to episodes
    print("\n1. Converting completed tasks to episodes...")
    episodes = convert_tasks_to_episodes(limit=200)
    print(f"   {len(episodes)} episodes created")

    # Show domain diversity
    domains = {}
    for ep in episodes:
        area = ep.get("source", {}).get("area", "unknown")
        domains.setdefault(area, 0)
        domains[area] += 1
    print(f"   Domains: {len(domains)}")
    for d, c in sorted(domains.items(), key=lambda x: -x[1])[:8]:
        print(f"     {d}: {c}")

    # Show sample
    print(f"\n   Sample episodes:")
    for ep in episodes[:3]:
        print(f"     {ep['episode_id']}: {ep['attributes']}")

    # Step 2: Run T11 frame expansion on diverse episodes
    print("\n2. T11 frame-expansion on diverse task episodes...")
    from ralph_portable.frame_expansion import run_frame_expansion
    t11_result = run_frame_expansion(episodes)
    print(f"   Episodes scanned: {t11_result['episodes_scanned']}")
    print(f"   mapping_exhausted signals: {len(t11_result['mapping_exhausted_signals'])}")
    print(f"   Recurring mismatches: {len(t11_result['recurring_mismatches'])}")
    print(f"   Proposed dimensions: {len(t11_result['proposed_dimensions'])}")
    print(f"   Promoted: {t11_result['promoted']} (DR12 enforced)")

    print(f"\n   Top recurring frame gaps:")
    for attr, eps in sorted(t11_result['recurring_mismatches'].items(), key=lambda x: -len(x[1]))[:10]:
        print(f"     {attr}: {len(eps)}/{len(episodes)} episodes")

    # Step 3: Run T10 on real principles
    print("\n3. T10 conceptual-inconsistency scan on real principles...")
    t10_report = run_t10_on_real_principles()
    print(f"   Principles: {t10_report['total_principles']}")
    print(f"   Pairs: {t10_report['total_pairs']}")
    print(f"   Candidates: {t10_report['candidate_pairs']}")
    print(f"   Conflicts: {t10_report['conflicts_found']}")
    for c in t10_report['classifications']:
        if c['classification'] != 'no_conflict':
            print(f"     {c['classification']}: {c['pair']}")

    # Step 4: Run T3 mining on diverse task episodes
    print("\n4. T3 mining on diverse task episodes...")
    mined_edges = run_t3_mining_on_task_episodes(episodes)
    print(f"   Mined edges: {len(mined_edges)}")
    for e in mined_edges[:5]:
        print(f"     {e.get('cause','?')} -> {e.get('effect','?')}")
        print(f"       tags: {e.get('tags', [])}")
        print(f"       formalization_hint: {e.get('formalization_hint','')[:80]}")

    # Step 5: Run T10 on T3-mined edges (the key test — can T10 find conflicts on diverse mined edges?)
    print("\n5. T10 on T3-mined edges (cross-domain conflict detection)...")
    if mined_edges:
        from ralph_portable.inconsistency_detector import scan_inconsistencies as _scan
        mined_principles = []
        for e in mined_edges:
            mined_principles.append({
                "principle_id": f"{e.get('cause','?')}->{e.get('effect','?')}",
                "principle_text": f"{e.get('cause','')} causes {e.get('effect','')}",
                "tags": e.get("tags", []),
                "status": "active", "is_active": True,
                "failure_modes_addressed": e.get("failure_modes_addressed", []),
                "formalization_hint": e.get("formalization_hint", ""),
            })
        t10_mined_report = _scan(mined_principles)
        print(f"   Mined principles: {t10_mined_report['total_principles']}")
        print(f"   Pairs: {t10_mined_report['total_pairs']}")
        print(f"   Candidates: {t10_mined_report['candidate_pairs']}")
        print(f"   Conflicts: {t10_mined_report['conflicts_found']}")
        for c in t10_mined_report['classifications']:
            if c['classification'] != 'no_conflict':
                print(f"     {c['classification']}: {c['pair']} - {c.get('scope','')}")
    else:
        print("   No mined edges to scan")
        t10_mined_report = {"conflicts_found": 0, "classifications": []}

    # Step 6: Save results
    print("\n6. Saving results...")
    output = {
        "timestamp": datetime.now().isoformat(),
        "episodes_count": len(episodes),
        "domains": domains,
        "t11": t11_result,
        "t10_principles": t10_report,
        "t3_mined_edges": len(mined_edges),
        "t10_mined": t10_mined_report,
    }
    os.makedirs("artifacts", exist_ok=True)
    with open("artifacts/jump_findings_diverse_real.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    print("   Saved to artifacts/jump_findings_diverse_real.json")

    # Summary
    print("\n" + "=" * 60)
    print("FINDINGS SUMMARY")
    print("=" * 60)
    print(f"""
Episodes: {len(episodes)} across {len(domains)} domains
T10 on hand-curated principles: {t10_report['conflicts_found']} conflicts
T10 on T3-mined edges: {t10_mined_report.get('conflicts_found', 0)} conflicts
T11 frame gaps: {len(t11_result['recurring_mismatches'])} recurring, {len(t11_result['proposed_dimensions'])} proposed
T3 mined edges: {len(mined_edges)} edges with tags/hints

Key question: did T10 find cross-domain conflicts on the T3-mined edges?
Answer: {t10_mined_report.get('conflicts_found', 0)} conflicts found
""")


if __name__ == "__main__":
    main()
