import { expect, test } from "./fixtures";

test.describe("pipeline editor", () => {
  test("a new pipeline opens as a graph on the canvas", async ({ signedIn: page }) => {
    await page.goto("/pipelines");
    const name = `E2E pipeline ${Date.now()}`;
    await page.getByLabel("Name").fill(name);
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText(name)).toBeVisible();

    await page.getByRole("link", { name: "Edit" }).first().click();
    await expect(page.locator(".react-flow")).toBeVisible();
    // The default graph: six nodes, rendered from what the API returned
    await expect(page.locator(".react-flow__node")).toHaveCount(6);
    await expect(page.getByText("Dense retrieval")).toBeVisible();
  });

  test("the inspector renders a node's config from its schema", async ({ signedIn: page }) => {
    await page.goto("/pipelines");
    await page.getByRole("link", { name: "Edit" }).first().click();
    // React Flow puts its controls over the canvas, so the click is forced
    // Dispatched rather than clicked: React Flow's controls and minimap sit
    // over the canvas, and which one covers a node depends on the viewport
    await page.locator('[data-testid="rf__node-dense"]').dispatchEvent("click");
    // The inspector form is generated from the node's JSON Schema: dense
    // retrieval declares a candidate count, so that field must appear
    const candidates = page.locator("#cfg-k");
    await expect(candidates).toBeVisible();
    await expect(candidates).toHaveValue("20");
    // The field's explanation comes from the schema too. Asserted as "there is
    // one, and it says something" rather than by quoting it: pinning the prose
    // here means every improvement to a description breaks a test that was
    // never about the wording.
    const description = candidates.locator("xpath=following-sibling::p[1]");
    await expect(description).toBeVisible();
    await expect(description).not.toBeEmpty();
  });
});
