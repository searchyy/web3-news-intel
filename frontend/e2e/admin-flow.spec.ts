import { test, expect } from "@playwright/test";

test("mocked admin Feishu group flow", async ({ page }) => {
  await page.route("**/api/admin/auth/login", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ authenticated: true, username: "admin", csrf_token: "csrf" })
    });
  });
  await page.route("**/api/admin/destinations", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "00000000-0000-0000-0000-000000000001",
          key: "feishu-app-test",
          name: "测试群",
          provider: "feishu_app",
          enabled: false,
          status: "pending",
          chat_name: "测试群"
        }
      ])
    });
  });
  await page.goto("/login");
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill("password");
  await page.getByRole("button", { name: "登录" }).click();
  await page.goto("/feishu-groups");
  await expect(page.getByText("测试群")).toBeVisible();
});
