# Hermetic unprivileged verifier

This directory is a standalone gate primitive shared by Autonomy Quest and Ralph. It runs the
**candidate commit's own code** while withholding every capability that could turn a test verdict
into an action.

## Trust boundary

There are three principals, not one credentialed CI process:

1. **Candidate runtime (untrusted):** fixed UID 65532, no network, no Linux capabilities, no new
   privileges, a read-only root, and only disposable tmpfs writes. It receives a read-only `git
   archive` of one resolved 40-character commit. It receives no host environment, DSN, token,
   socket, SSH agent, Git metadata, Docker socket, or writable remote.
2. **Gate signer (trusted wrapper):** observes Docker's exit status outside the container and signs a
   canonical verdict with an Ed25519 gate key. The private key is never mounted into the candidate.
   Candidate stdout is untrusted log data and cannot create a verdict.
3. **Actuator (separate service identity):** owns the Git push credential, but only a public gate key.
   It must verify a `PASS` for the exact repository and SHA before acting. This package deliberately
   contains no `git push` actuator.

A host that gives the gate signer a push credential collapses this separation. Run it under a service
account whose only secret is the gate-signing key. Run the actuator under a different account with no
signing key.

## Build and run

Linux containers are the enforcement mechanism (Docker Desktop is sufficient for the included
control proof):

```sh
docker build -t aq-hermetic-verifier:local hermetic_verifier
umask 077
openssl genpkey -algorithm ED25519 -out /secure/gate-private.pem
openssl pkey -in /secure/gate-private.pem -pubout -out /secure/gate-public.pem

python hermetic_verifier/run.py \
  --repo /path/to/repo --sha "$CANDIDATE_SHA" \
  --signing-key /secure/gate-private.pem --key-id close-loop-gate-v1 \
  -- python -m pytest -q > verdict.json
```

The default command is `python -m pytest -q`. A custom prebuilt image may be supplied with `--image`;
it must preserve the trusted entrypoint contract and contain all dependencies because test-time
network access is impossible. Build dependencies ahead of time; do not pass package-registry tokens.

The wrapper resolves the commit, exports it with `git archive` (excluding the credential-bearing
`.git` directory), and signs the image ID, exact command, policy, timestamps, nonce, exit status, repo,
and commit. Exit 0 means signed `PASS`, 1 means signed `FAIL`, and 2 means signed `ERROR` or an unsigned
rig failure. Candidate pytest output and candidate-selected exit status are not verdict inputs. PID 1
loads the root-owned `verifier_census` plugin explicitly from the image, reads its structured census
through a pre-created tmpfs file descriptor, and requires a clean session with at least one collected
and passed test. Missing, malformed, vacuous, failed, errored, and all-skipped censuses cannot become
`PASS`.

An actuator can validate without receiving the signing key:

```sh
python hermetic_verifier/verify_verdict.py \
  --public-key /secure/gate-public.pem \
  --repository "$(git -C /path/to/repo config --get remote.origin.url)" \
  --sha "$(git -C /path/to/repo rev-parse "$CANDIDATE_SHA^{commit}")" \
  --test-plan-digest "$APPROVED_TEST_PLAN_DIGEST" \
  --image-id "$APPROVED_IMAGE_ID" \
  --entrypoint-digest "$APPROVED_ENTRYPOINT_DIGEST" \
  --policy-digest "$APPROVED_POLICY_DIGEST" \
  --key-id close-loop-gate-v1 --nonce-store /var/lib/aq/used-verdict-nonces \
  verdict.json
```

The verifier enforces all authorization bindings itself: exact repository, full SHA, test-plan
digest, immutable image ID, entrypoint identity, effective-policy digest, signing key ID, expiry,
and atomic replay-nonce consumption. The approved values must come from actuator configuration,
never from the verdict being checked.

## Live red/green acceptance proof

Run:

```sh
python hermetic_verifier/prove_controls.py
```

The proof uses the same deliberately hostile pytest file twice:

* the hermetic runtime must report `BLOCKED` for Git push, a control-plane table insert, actuator
  token/socket use, an outside write, and network egress; its honest test also passes;
* zero-test repositories fail even when candidate conftest code forces exit zero, prints a forged
  passing summary, or tries to pre-create the census path;
* a deliberately naive credentialed/networked runner must report `ALLOWED` for all five, and the
  proof inspects exact external side effects (Git ref contents, DB row, actuator receipt, outside
  file, and HTTP receipt).

The script rejects skips, missing result content, wrong signatures, or missing side-effect content.
This is a contrast proof, not an assertion that flags look secure.

## Limitations and operator obligations

* The Docker daemon and trusted image are in the trusted computing base. Do not mount its socket.
* This is Linux-container isolation, not a hostile multi-tenant VM boundary. Use a microVM where the
  daemon/kernel threat model requires it.
* The base image is digest pinned. Custom images must likewise be immutable and reviewed.
* Candidate submodules are not included by `git archive`; vendor them or provide a reviewed image and
  source materialization step before using this primitive.
* Keep verdict keys outside the repository, mode 0600, and rotate/revoke them independently of Git
  push credentials.
