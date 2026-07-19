# Task 3303 Fail-First Inspection Evidence

Date: 2026-07-19
Worker: cxf-3303-approve-execute-auth-ceiling

Observed current behavior before any code edits:
- ui/server.py POST /api/approve/{id} updates work.status from awaiting_human to pending and sets approved_at, but does not authenticate the request.
- runner/loop.py Loop.cycle observes open_work but does not claim or execute existing pending work before calling prompts.decide().
- runner/db.py resume_signal treats approved pending work as a hibernation wake signal only; it does not execute the approved work.

Fail-first test to preserve after checkpoint:
1. Seed work row with status=awaiting_human.
2. Approve it through /api/approve or set status=pending, approved_at=now().
3. Run one Loop.cycle with a fake executor.
4. Expected current failure: fake executor receives DECIDE for new work instead of ACT for approved work; approved work remains pending/no run for that work.

This artifact was created before editing runner/loop.py or ui/server.py, per checkpoint constraints.
