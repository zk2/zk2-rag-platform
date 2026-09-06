import { test as base, expect, type Page } from "@playwright/test";

/**
 * Credentials come from the environment, never from a file in the repository.
 * CI seeds this account before the suite runs; locally they are the
 * super-admin from .env.
 */
export const CREDENTIALS = {
  email: process.env.E2E_EMAIL ?? process.env.SUPER_ADMIN_EMAIL ?? "",
  password: process.env.E2E_PASSWORD ?? process.env.SUPER_ADMIN_PASSWORD ?? "",
};

export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(CREDENTIALS.email);
  await page.getByLabel("Password").fill(CREDENTIALS.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // A super-admin lands on the access-request queue, everyone else on the
  // dashboard. Either way the workspace is one navigation away - the seeded
  // account is a member of an organization, which is what the app zone needs.
  await expect(page).toHaveURL(/\/(dashboard|admin\/access-requests)/);
  await page.goto("/dashboard");
  await expect(page.getByRole("navigation").getByRole("link", { name: "Sources" })).toBeVisible();
}

/** Where the app keeps the session; replayed rather than re-earned below. */
const STORAGE_KEY = "zk2-auth";

/**
 * One login per worker process, cached here.
 *
 * The API allows ten logins a minute from one address
 * (RATE_LIMIT_LOGIN_PER_MIN) and the whole suite now runs inside that window,
 * so signing in for every test spent the budget and the last tests were
 * refused. Nothing in these specs is about the login form except auth.spec,
 * which still drives it for real.
 */
let session: string | null = null;

/** A signed-in page, for the specs that are not about signing in. */
export const test = base.extend<{ signedIn: Page }>({
  signedIn: async ({ page, browser, baseURL }, use) => {
    if (session === null) {
      const context = await browser.newContext({ baseURL });
      const first = await context.newPage();
      await signIn(first);
      session = await first.evaluate(
        (key) => window.localStorage.getItem(key) ?? "",
        STORAGE_KEY,
      );
      await context.close();
    }
    await page.addInitScript(
      ([key, value]) => window.localStorage.setItem(key, value),
      [STORAGE_KEY, session],
    );
    await use(page);
  },
});

export { expect };
