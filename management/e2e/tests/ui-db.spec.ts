import { test, expect } from "@playwright/test";

// Task #4407 — Playwright validation of the Ralph-control management UI AND the
// database behind it. Runs against a live container (AQ_MGMT_URL). The React shell
// is served OOTB by the FastAPI management API; state is Postgres-backed (v6).
//
// "Validate the UI and database" means: the UI renders the real surfaces, and a
// write made THROUGH the UI lands in Postgres — proven by (a) the row surviving a
// full page reload (no client state) and (b) the API/state (which is postgres-backed)
// reflecting the new row.

test.describe("management shell UI + DB", () => {
  test("React shell renders with DB-backed (postgres) state", async ({ page }) => {
    await page.goto("/");

    // The built React shell mounts into #root (not the interim vanilla console).
    await expect(page.locator("#root")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "AQ Ralph Control — Management" })
    ).toBeVisible();

    // Health badge resolves to ok (API reachable).
    await expect(page.getByText("health: ok")).toBeVisible();

    // The Ralph-state card shows the durable backing + the manager-gated merge gate.
    const stateCard = page.locator(".card", { hasText: "Ralph state" });
    await expect(stateCard.getByText("postgres", { exact: true })).toBeVisible();
    await expect(stateCard.getByText("manager_gated", { exact: true })).toBeVisible();
    // Replication is operator-gated by default (no host OVERRIDE) — the honest safe default.
    await expect(stateCard.getByText("operator-gated", { exact: true })).toBeVisible();
  });

  test("creating a task through the UI persists to Postgres (survives reload + shows in DB-backed API)", async ({
    page,
    request,
  }) => {
    const title = `pw-e2e task ${Date.now()}`;

    // Baseline count straight from the postgres-backed API.
    const before = await (await request.get("/api/tasks")).json();
    const beforeCount = before.items.length as number;

    await page.goto("/");

    // Fill + submit the Create-task form (real user path, not a direct fetch).
    const createCard = page.locator(".card", { hasText: "Create task" });
    await createCard.getByRole("textbox").first().fill(title); // "title" input
    await createCard.getByRole("button", { name: "Create" }).click();

    // UI reflects the write.
    await expect(createCard.getByText(/created →/)).toBeVisible();
    const tasksCard = page.locator(".card", { hasText: "Tasks" });
    await expect(tasksCard.getByText(title)).toBeVisible();

    // DB proof #1: reload the page (no client state) — the row must still render,
    // which means it came back from Postgres via the API, not from memory.
    await page.reload();
    await expect(
      page.locator(".card", { hasText: "Tasks" }).getByText(title)
    ).toBeVisible();

    // DB proof #2: the postgres-backed API reflects exactly one new row with our title,
    // and state.backing is postgres (so this really is the durable store).
    const after = await (await request.get("/api/tasks")).json();
    expect(after.items.length).toBe(beforeCount + 1);
    expect(after.items.some((t: { title: string }) => t.title === title)).toBeTruthy();

    const state = await (await request.get("/api/ralph/state")).json();
    expect(state.backing).toBe("postgres");
    expect(state.counts.tasks).toBe(after.items.length);
  });

  test("agent-comms posted via the AUTHENTICATED API renders in the UI list (comms surface alive)", async ({
    page,
    request,
  }) => {
    // Phase 0 deprecated the UNAUTHENTICATED POST (now 401): identity is derived from the credential
    // and the body carries only routing + content. Authenticate with the operator credential (it may
    // publish operator.message on any channel). Skipped — green — where no token is provisioned.
    const token = process.env.AQ_COMMS_OPERATOR_TOKEN;
    test.skip(!token, "set AQ_COMMS_OPERATOR_TOKEN to exercise the authenticated comms POST");

    const text = `pw-e2e comm ${Date.now()}`;
    const r = await request.post("/api/agent-comms", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        channel: "fleet/ops",
        kind: "operator.message",
        payload: { text },
        target: { handle: "reviewer" },
      },
    });
    expect(r.ok()).toBeTruthy();

    await page.goto("/");
    // The typed CommsPanel renders the projected row's text — our message must appear.
    await expect(
      page.locator(".card", { hasText: "Agent comms" }).getByText(new RegExp(text))
    ).toBeVisible();
  });
});
