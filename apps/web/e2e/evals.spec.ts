import { expect, test } from "./fixtures";

test.describe("evals", () => {
  test("a golden set can be created and imported", async ({ signedIn: page }) => {
    await page.goto("/evals");
    const name = `E2E dataset ${Date.now()}`;
    await page.getByLabel("New dataset").fill(name);
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText(name)).toBeVisible();

    await page.setInputFiles('input[type="file"]', {
      name: "golden.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "question,expected_answer,expected_sources,tags\nHow many days?,20 days,e2e-handbook.txt,smoke\n",
      ),
    });
    await expect(page.getByText(/imported 1/i)).toBeVisible();
  });

  test("judge metrics are marked as costing money", async ({ signedIn: page }) => {
    await page.goto("/evals");
    await page.getByText(/E2E dataset/).first().click();
    await expect(page.getByRole("button", { name: /faithfulness/ })).toBeVisible();
  });
});
