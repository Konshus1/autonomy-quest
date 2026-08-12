import { test, expect } from "@playwright/test";

// #4834 comms Phase 1 — Playwright validation of the FLEET / TOPOLOGY view (design §9.2 + §10).
//
// Two things are proven here:
//  1. N3 operator-gating: with no operator token the Fleet card is honestly LOCKED (an unlock
//     prompt), not an empty/fake fleet — the view is not world-readable on the mgmt port.
//  2. Host-observed rendering + a live -> stale -> down transition: the fleet table renders the
//     host-authoritative topology and its health badges, and updates on the next poll when the
//     host observation changes.
//
// The fleet response is served via route interception so the UI render + transition are tested
// deterministically, independent of a running host poller / real replicas. (The poller-writes ->
// journal -> /api/fleet path itself is proven by the Python tests.)

const OP_TOKEN = "e2e-operator-token";

function fleetPayload(healthState: "live" | "stale" | "down", extra: Record<string, unknown> = {}) {
  return {
    ok: true,
    count: 1,
    items: [
      {
        instance_id: "urn:uuid:replica-1",
        project: "aq-replica-1",
        parent_instance_id: "urn:uuid:parent",
        lineage: ["urn:uuid:parent", "urn:uuid:replica-1"],
        created_at: "2026-01-01T00:00:00+00:00",
        workflow: "flagship",
        workflow_version: "v1",
        git_sha: "cafef00dbabe",
        observed_git_sha: "cafef00dbabe",
        expected_git_sha: "cafef00dbabe",
        health_state: healthState,
        reachable: healthState === "live",
        reason: healthState === "live" ? "reachable" : "/health unreachable at 127.0.0.1:5104",
        cycling: healthState === "live",
        seconds_since_last_cycle: 12,
        observed_at: "2026-01-01T00:05:00+00:00",
        last_successful_poll: "2026-01-01T00:00:00+00:00",
        trust: "host_observed",
        app_mgmt_port: 5104,
        endpoint_redacted: "127.0.0.1:5104 (host-local)",
        lifecycle_state: "live",
        torn_down: false,
        counts_against_cap: true,
        credential_present: true,
        credential_revoked: false,
        message_count: 3,
        health_observation_count: 3,
        ...extra,
      },
    ],
  };
}

test.describe("fleet / topology view", () => {
  test("fleet is operator-gated when no token is present (N3)", async ({ page }) => {
    await page.addInitScript(() => window.localStorage.removeItem("aq.comms.operatorToken"));
    await page.goto("/");
    const card = page.locator(".card", { hasText: "Fleet / topology" });
    await expect(card).toBeVisible();
    // Locked: an unlock prompt, not a fleet table.
    await expect(card.getByText(/not world-readable on the mgmt port/)).toBeVisible();
    await expect(card.getByRole("button", { name: "Unlock fleet" })).toBeVisible();
    await expect(card.locator("[data-testid=fleet-table]")).toHaveCount(0);
  });

  test("renders host-observed topology and a live -> stale -> down transition", async ({ page }) => {
    // Seed the operator token so the panel unlocks and calls GET /api/fleet.
    await page.addInitScript(
      (t) => window.localStorage.setItem("aq.comms.operatorToken", t),
      OP_TOKEN
    );

    // Serve a controllable host-observed fleet response.
    let state: "live" | "stale" | "down" = "live";
    await page.route("**/api/fleet", async (route) => {
      // The panel must send the operator credential (N3) — prove it does.
      const token = route.request().headers()["x-aq-comms-token"];
      expect(token).toBe(OP_TOKEN);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(fleetPayload(state)),
      });
    });

    await page.goto("/");
    const card = page.locator(".card", { hasText: "Fleet / topology" });
    const row = card.locator("[data-testid=fleet-row-urn:uuid:replica-1]");

    // Host-authoritative topology renders: instance/project, redacted host-local port (not a URL).
    await expect(row).toBeVisible();
    await expect(row.getByText("aq-replica-1")).toBeVisible();
    await expect(row.getByText("127.0.0.1:5104 (host-local)")).toBeVisible();
    // live health badge.
    await expect(card.locator("[data-testid=health-urn:uuid:replica-1]")).toHaveText("live");

    // Transition 1: live -> stale. The 8s poll picks up the new host observation.
    state = "stale";
    await expect(card.locator("[data-testid=health-urn:uuid:replica-1]")).toHaveText("stale", {
      timeout: 12_000,
    });

    // Transition 2: stale -> down.
    state = "down";
    await expect(card.locator("[data-testid=health-urn:uuid:replica-1]")).toHaveText("down", {
      timeout: 12_000,
    });
  });
});
