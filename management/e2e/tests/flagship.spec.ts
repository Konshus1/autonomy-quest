import { test, expect } from "@playwright/test";

const expectMeasureError = process.env.AQ_EXPECT_MEASURE_ERROR === "1";

test("flagship renders mission, real measure and target", async ({ page, request }) => {
  test.skip(expectMeasureError, "negative-control mode");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  const card = page.getByTestId("flagship-mission");
  await expect(card).toBeVisible();
  await expect(card.getByRole("heading", { name: "Get to 20 paying customers by the end of Q3" })).toBeVisible();
  await expect(card.getByText("count of active paying customers", { exact: false })).toBeVisible();
  await expect(card.getByText("20", { exact: true })).toBeVisible();
  await expect(card.getByText("0", { exact: true })).toBeVisible();
  await expect(card.getByText("not overshooting", { exact: true })).toBeVisible();
  const body = await (await request.get("/api/flagship")).json();
  expect(body.mission.now).toBe(0);
  expect(body.mission.error).toBeNull();
  expect(body.mission.target).toBe(20);
  expect(errors).toEqual([]);
});

test("broken measure renders ERROR, never zero", async ({ page, request }) => {
  test.skip(!expectMeasureError, "baseline mode");
  await page.goto("/");
  const card = page.getByTestId("flagship-mission");
  await expect(card.getByRole("alert")).toContainText("Measure query failed");
  await expect(card.getByRole("alert")).toContainText("does not exist");
  await expect(card.getByText("ERROR", { exact: true })).toBeVisible();
  const body = await (await request.get("/api/flagship")).json();
  expect(body.mission.now).toBeNull();
  expect(body.mission.error).toContain("does not exist");
});


test("persisted cycle rationale, outcome and cost render", async ({ page, request }) => {
  test.skip(process.env.AQ_EXPECT_SAMPLE_TRAIL !== "1", "requires clearly-labelled sample trail seed");
  await page.goto("/");
  const history = page.getByTestId("cycle-history");
  await expect(history.getByText("SAMPLE_TEST_ONLY_M3 work item", { exact: true })).toBeVisible();
  await expect(history.getByText("SAMPLE_TEST_ONLY_M3 rationale persisted before act", { exact: false })).toBeVisible();
  await expect(history.getByText("SAMPLE_TEST_ONLY_M3 observed outcome", { exact: false })).toBeVisible();
  await expect(history.getByText("$0.4200", { exact: true })).toBeVisible();
  const body = await (await request.get("/api/flagship")).json();
  expect(body.runs[0].rationale).toBe("SAMPLE_TEST_ONLY_M3 rationale persisted before act");
  expect(Number(body.runs[0].cost_usd)).toBe(0.42);
});

test("durable live learning trail renders evidence and status", async ({ page, request }) => {
  test.skip(process.env.AQ_EXPECT_SAMPLE_TRAIL !== "1", "requires clearly-labelled sample trail seed");
  await page.goto("/");
  const trail = page.getByTestId("learnings-trail");
  await expect(trail.getByText("SAMPLE_TEST_ONLY_M4 learning", { exact: true })).toBeVisible();
  await expect(trail.getByText("SAMPLE_TEST_ONLY_M4 evidence", { exact: false })).toBeVisible();
  await expect(trail.getByText(/scope local · confidence 0.7/)).toBeVisible();
  const body = await (await request.get("/api/flagship")).json();
  expect(body.learnings[0].evidence).toBe("SAMPLE_TEST_ONLY_M4 evidence");
});
