import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * Upload the way a person does: the File tab, then the button that opens the
 * picker. The input itself is hidden, which is also why setInputFiles on it
 * cannot work.
 */
async function upload(
  page: Page,
  file: { name: string; mimeType: string; buffer: Buffer },
): Promise<void> {
  await page.getByRole("button", { name: "File", exact: true }).click();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose file" }).click();
  await (await chooser).setFiles(file);
}

test.describe("workspace", () => {
  test("a document can be uploaded and appears in the tree", async ({ signedIn: page }) => {
    await page.goto("/sources");
    await upload(page, {
      name: "e2e-handbook.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Employees accrue 20 vacation days per year."),
    });
    // first(): earlier runs may have left a document with the same name
    await expect(page.getByText("e2e-handbook.txt").first()).toBeVisible();
  });

  test("an unsupported file is refused with a reason", async ({ signedIn: page }) => {
    await page.goto("/sources");
    await upload(page, {
      name: "payload.exe",
      mimeType: "application/octet-stream",
      buffer: Buffer.from("MZ"),
    });
    await expect(page.getByText(/unsupported file type/i)).toBeVisible();
  });

  test("a bot can be created and offers models from the catalog", async ({ signedIn: page }) => {
    await page.goto("/bots");
    const name = `E2E bot ${Date.now()}`;
    await page.getByLabel("Name").fill(name);
    // The model list is served by the API, not hardcoded in the page
    await expect(page.locator("#bot-model option").first()).toBeAttached();
    await page.getByRole("button", { name: "Create bot" }).click();
    await expect(page.getByText(name)).toBeVisible();
  });

  test("the shared-key allowance is shown before it bites", async ({ signedIn: page }) => {
    await page.goto("/settings/providers");
    // The banner states the remaining allowance in tokens
    await expect(page.getByText(/Shared keys: .* of .* tokens used/i)).toBeVisible();
  });
});
