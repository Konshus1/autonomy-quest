"""Fleet/topology projection from the parent journal (#4834 comms Phase 1, design §9.2).

The Phase-1 host poller (``scripts/host_fleet_poller.py``, host-only) observes each replica's
``/health`` and writes a ``health.observed`` envelope — with ``trust=host_observed`` and a
``topology`` block — into the parent journal (the comms store). This module projects those
envelopes into the operator fleet view.

Deliberately DERIVED FROM THE JOURNAL, not the registry. The host-owned ``fleet_registry`` is
host-only and MUST NOT be importable from a guest-reachable module (the import firewall forbids it).
The journal is the hand-off: the host writes topology+health into it as ground-truth envelopes, and
this guest-reachable projection reads only the journal. So the management API can serve the fleet
view without ever importing the host registry — the firewall stays intact.

Only ``trust=host_observed`` ``health.observed`` envelopes drive fleet HEALTH. A replica-authored
``health.observed`` (Phase 2) is ``trust=untrusted_claim`` and is ignored here — it can never
overwrite the host observation (design §10 Phase 1 invariant).
"""

from __future__ import annotations

from typing import Any, Iterable

HEALTH_OBSERVED_KIND = "health.observed"
HOST_OBSERVED_TRUST = "host_observed"


def _observed_instance_id(env: dict[str, Any]) -> str | None:
    """The replica an observation is ABOUT: the topology block, else the envelope target."""
    payload = env.get("payload") or {}
    topo = payload.get("topology") or {}
    iid = topo.get("instance_id")
    if iid:
        return iid
    target = env.get("target") or {}
    return target.get("instance_id")


def _instance_of_envelope(env: dict[str, Any]) -> str | None:
    """Any instance an envelope references (for message counting): the observed instance, the
    target, or an ``instance/<id>/...`` channel scope."""
    iid = _observed_instance_id(env)
    if iid:
        return iid
    target = env.get("target") or {}
    if target.get("instance_id"):
        return target["instance_id"]
    channel = env.get("channel") or ""
    parts = channel.split("/")
    if len(parts) >= 2 and parts[0] == "instance":
        return parts[1]
    return None


def redacted_endpoint(port: Any) -> str | None:
    """A host-local port rendered as non-clickable text, never an arbitrary clickable URL
    (design §9.2: "management route as a redacted host-local port")."""
    if port is None:
        return None
    return f"127.0.0.1:{port} (host-local)"


def _project_one(instance_id: str, latest: dict[str, Any], message_count: int,
                 observation_count: int) -> dict[str, Any]:
    payload = latest.get("payload") or {}
    topo = payload.get("topology") or {}
    observed_sha = payload.get("observed_git_sha")
    expected_sha = topo.get("expected_git_sha")
    return {
        "instance_id": instance_id,
        "project": topo.get("project"),
        "parent_instance_id": topo.get("parent_instance_id"),
        "lineage": list(topo.get("lineage") or []),
        "created_at": topo.get("created_at"),
        "workflow": topo.get("workflow"),
        "workflow_version": topo.get("workflow_version"),
        # What is deployed: prefer the SHA the host actually observed on /health; fall back to
        # the SHA the registry expected. Both are shown so a mismatch is legible.
        "git_sha": observed_sha or expected_sha,
        "observed_git_sha": observed_sha,
        "expected_git_sha": expected_sha,
        # Host-observed health (ground truth — the observer, not a replica claim).
        "health_state": payload.get("health_state"),
        "reachable": bool(payload.get("reachable")),
        "reason": payload.get("reason"),
        "cycling": payload.get("cycling"),
        "seconds_since_last_cycle": payload.get("seconds_since_last_cycle"),
        "observed_at": payload.get("observed_at") or latest.get("created_at"),
        "last_successful_poll": payload.get("last_successful_poll"),
        "trust": latest.get("trust"),
        # Redacted host-local management port — NOT a clickable arbitrary URL.
        "app_mgmt_port": topo.get("app_mgmt_port"),
        "endpoint_redacted": redacted_endpoint(topo.get("app_mgmt_port")),
        # Lifecycle + teardown + cap accounting (design §9.2).
        "lifecycle_state": topo.get("lifecycle_state"),
        "torn_down": topo.get("lifecycle_state") == "torn_down",
        "counts_against_cap": bool(topo.get("counts_against_cap")),
        "credential_present": bool(topo.get("credential_present")),
        "credential_revoked": bool(topo.get("credential_revoked")),
        # Journal accounting.
        "message_count": message_count,
        "health_observation_count": observation_count,
    }


def fleet_from_envelopes(envelopes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the parent journal into the host-authoritative fleet/topology view.

    Input envelopes are assumed monotonic (ULID/seq ascending — both stores order by insertion),
    so for each instance the LAST ``host_observed`` ``health.observed`` envelope is its current
    state. Ordering the result by ``created_at`` keeps the fleet list stable across polls.
    """
    envelopes = list(envelopes)
    latest_obs: dict[str, dict[str, Any]] = {}
    obs_count: dict[str, int] = {}
    msg_count: dict[str, int] = {}

    for env in envelopes:
        # Message counting spans every envelope that references an instance (any kind/trust).
        ref = _instance_of_envelope(env)
        if ref:
            msg_count[ref] = msg_count.get(ref, 0) + 1
        # Health is driven ONLY by host-observed health.observed events.
        if env.get("kind") != HEALTH_OBSERVED_KIND or env.get("trust") != HOST_OBSERVED_TRUST:
            continue
        iid = _observed_instance_id(env)
        if not iid:
            continue
        obs_count[iid] = obs_count.get(iid, 0) + 1
        latest_obs[iid] = env  # ascending order => last write wins == latest observation

    fleet = [
        _project_one(iid, env, msg_count.get(iid, 0), obs_count.get(iid, 0))
        for iid, env in latest_obs.items()
    ]
    fleet.sort(key=lambda f: (f.get("created_at") or "", f["instance_id"]))
    return fleet
