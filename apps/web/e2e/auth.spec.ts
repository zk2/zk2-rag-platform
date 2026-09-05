import { expect, test } from "@playwright/test";
import { CREDENTIALS, signIn } from "./fixtures";

test.describe("authentication", () => {
  test("the landing page says access is invite-only", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "zk2-chatbot" })).toBeVisible();
    await expect(page.getByText(/invite-only/i)).toBeVisible();
  });

  test("there is no way to sign up", async ({ page }) => {
    // Self-signup is disabled by design, not hidden behind a flag
    await page.goto("/");
    await expect(page.getByRole("link", { name: /request access/i }).first()).toBeVisible();
    expect(await page.getByRole("link", { name: /^sign up$/i }).count()).toBe(0);
  });

  test("a wrong password is rejected", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(CREDENTIALS.email);
    await page.getByLabel("Password").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText(/invalid credentials/i)).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("the right password gets in", async ({ page }) => {
    await signIn(page);
    await expect(page.getByRole("navigation").getByRole("link", { name: "Sources" })).toBeVisible();
  });
});
