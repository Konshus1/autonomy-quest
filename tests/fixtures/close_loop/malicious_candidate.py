"""Adversarial verifier payload used only by the mandatory sandbox control."""
import json
import os
from pathlib import Path
import socket
import sys


def overwrite(path, value="CORRUPTED"):
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        return "wrote"
    except BaseException as exc:
        return type(exc).__name__


def attack(host_target):
    result = {
        "control_file": overwrite("/candidate/control/manifest.json"),
        "remote_ref": overwrite("/candidate/.git/refs/remotes/origin/main"),
        "candidate_persistence": overwrite("/candidate/persisted-by-candidate"),
        "host_absolute": overwrite(host_target),
        "host_alias": overwrite("/host/persisted-by-candidate"),
        "root_persistence": overwrite("/persisted-by-candidate"),
        "tmp_write": overwrite("/tmp/aq-close-loop-disposable", "ephemeral"),
        "credential_canary": os.environ.get("AQ_CLOSE_LOOP_CANARY_SECRET"),
        "credential_names": sorted(
            name for name in os.environ
            if name.startswith(("AWS_", "AZURE_", "GITHUB_", "AQ_TOKEN", "AQ_SECRET"))
        ),
        "docker_socket_exists": any(
            Path(path).exists() for path in ("/var/run/docker.sock", "/run/docker.sock")
        ),
    }
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.2)
        client.connect("/var/run/docker.sock")
        result["docker_socket_connect"] = "connected"
    except BaseException as exc:
        result["docker_socket_connect"] = type(exc).__name__
    finally:
        try:
            client.close()
        except BaseException:
            pass
    try:
        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote.settimeout(0.4)
        remote.connect(("1.1.1.1", 53))
        result["external_network"] = "connected"
    except BaseException as exc:
        result["external_network"] = type(exc).__name__
    finally:
        try:
            remote.close()
        except BaseException:
            pass
    print(json.dumps(result, sort_keys=True))


def probe():
    print(json.dumps({
        "tmp_survived": Path("/tmp/aq-close-loop-disposable").exists(),
        "candidate_survived": Path("/candidate/persisted-by-candidate").exists(),
    }, sort_keys=True))


if sys.argv[1] == "attack":
    attack(sys.argv[2])
elif sys.argv[1] == "probe":
    probe()
else:
    raise SystemExit("usage: malicious_candidate.py attack HOST_PATH | probe")
