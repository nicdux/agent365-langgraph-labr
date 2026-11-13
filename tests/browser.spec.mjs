import { test, expect } from "@playwright/test";

const STORAGE_KEY = "eduardo-professional-chat-v1";
const official = "https://learn.microsoft.com/en-us/graph/overview";
const reply = {
  answer: "O **Microsoft Graph** integra serviços Microsoft.",
  grounded: false,
  sources: [{ id: "graph", title: "Microsoft Graph", label: "Microsoft Learn", url: official }],
};
const session = {
  csrf_token: "synthetic-csrf",
  profile: { name: "Perfil de teste", initials: "EC", reference_url: "https://example.com/profile" },
  approved_entries: 0,
  scope: "microsoft",
  model_configured: true,
  limits: { message_chars: 2000, history_messages: 10, history_chars: 12000, session_messages: 40 },
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/session", (route) => route.fulfill({ json: session }));
});

async function open(page) {
  await page.goto("/");
  await expect(page.getByText("Pronto · Microsoft Technologies", { exact: true })).toBeVisible();
}

async function darkState(page) {
  return page.evaluate(() => {
    const whiteBackgrounds = [...document.querySelectorAll("body *")]
      .filter((node) => node.getClientRects().length)
      .filter((node) => {
        const rgba = getComputedStyle(node).backgroundColor.match(/[\d.]+/g)?.map(Number);
        return rgba && (rgba.length < 4 || rgba[3] > .8)
          && rgba.slice(0, 3).every((value) => value > 210);
      }).length;
    return {
      scheme: getComputedStyle(document.documentElement).colorScheme,
      background: getComputedStyle(document.body).backgroundColor,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      whiteBackgrounds,
    };
  });
}

test("both tabs stay dark and responsive, even with system light preference", async ({ page }) => {
  await open(page);
  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const name of ["O laboratório", "Pergunte ao Eduardo"]) {
      await page.getByRole("tab", { name, exact: true }).click();
      expect(await darkState(page)).toEqual({
        scheme: "dark", background: "rgb(5, 5, 5)", overflow: false, whiteBackgrounds: 0,
      });
    }
  }
});

test("Markdown, code highlighting, safe links and keyboard copy", async ({ page }) => {
  const markdown = [
    "## Microsoft Graph", "**Exemplo**", "- Consulte identidades.",
    "```powershell\n$user = Get-MgUser -Top 1\nWrite-Output $user.DisplayName\n```",
    "| Recurso | Uso |\n| --- | --- |\n| Graph | Integração |",
    `[Documentação oficial](${official})`,
    "[Link não autorizado](https://example.com/unknown)",
    '<script>globalThis.__xss = true</script><img src="https://example.com/pixel" onerror="globalThis.__xss = true">',
  ].join("\n\n");
  await page.addInitScript(() => {
    globalThis.__xss = false;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (text) => { globalThis.__copied = text; } },
    });
  });
  await page.route("**/api/chat", (route) => route.fulfill({ json: { ...reply, answer: markdown } }));
  await open(page);
  await page.getByRole("textbox").fill("Mostre um exemplo Microsoft Graph.");
  await page.getByRole("textbox").press("Enter");
  await expect(page.locator(".markdown h2")).toHaveText("Microsoft Graph");
  await expect(page.locator(".hljs-variable")).toHaveCount(2);
  await expect(page.locator(".table-scroll table")).toBeVisible();
  await expect(page.locator(".markdown img, .markdown script, .markdown [onerror]")).toHaveCount(0);
  await expect(page.locator(".markdown a")).toHaveCount(1);
  await expect(page.locator(".markdown a")).toHaveAttribute("href", official);
  const copy = page.getByRole("button", { name: "Copiar resposta", exact: true });
  await copy.focus();
  await copy.press("Enter");
  await expect(page.getByText("Resposta copiada.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => globalThis.__copied)).toBe(markdown);
  expect(await page.evaluate(() => globalThis.__xss)).toBe(false);
  expect((await darkState(page)).whiteBackgrounds).toBe(0);
});

test("loading, cancellation and errors preserve the draft without late replies", async ({ page }) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/chat", async (route) => {
    await gate;
    await route.fulfill({ json: reply });
  });
  await open(page);
  await page.getByRole("textbox").fill("Explique Microsoft Graph.");
  await page.getByRole("textbox").press("Enter");
  await expect(page.getByText("Analisando a pergunta e validando a resposta…", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox")).toBeDisabled();
  await page.getByRole("button", { name: "Limpar conversa e começar novamente" }).click();
  release();
  await expect(page.locator(".message")).toHaveCount(0);
  await page.unroute("**/api/chat");
  await page.route("**/api/chat", (route) => route.fulfill({
    status: 502, json: { error: { code: "model_unavailable", message: "Serviço sintético indisponível." } },
  }));
  await page.getByRole("textbox").fill("Pergunta Microsoft preservada");
  await page.getByRole("textbox").press("Enter");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveValue("Pergunta Microsoft preservada");
  await expect(page.getByRole("textbox")).toBeEnabled();
  await expect(page.locator(".message")).toHaveCount(0);
  expect((await darkState(page)).whiteBackgrounds).toBe(0);
});

test("session history survives reload and respects display/model budgets", async ({ page }) => {
  let posted;
  await page.route("**/api/chat", (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: reply });
  });
  await open(page);
  await page.evaluate((key) => {
    const history = [];
    for (let index = 0; index < 20; index++) {
      const time = new Date().toISOString();
      history.push(
        { role: "user", content: `Microsoft question ${index}`, sources: [], time },
        { role: "assistant", content: `Microsoft answer ${index}`, sources: [], time },
      );
    }
    sessionStorage.setItem(key, JSON.stringify(history));
  }, STORAGE_KEY);
  await page.reload();
  await expect(page.locator(".message")).toHaveCount(40);
  await page.getByRole("textbox").fill("Nova pergunta Microsoft");
  await page.getByRole("textbox").press("Enter");
  await expect(page.locator(".message-assistant").last()).toContainText("Microsoft Graph");
  expect(posted.history).toHaveLength(10);
  expect(posted.history[0].content).toBe("Microsoft question 15");
  await expect(page.locator(".message")).toHaveCount(40);
  await page.reload();
  await expect(page.locator(".message")).toHaveCount(40);
  await page.getByRole("button", { name: "Limpar conversa e começar novamente" }).click();
  expect(await page.evaluate((key) => sessionStorage.getItem(key), STORAGE_KEY)).toBeNull();
});

test("labels, focus, hover, contrast and reduced motion are consistent", async ({ page }) => {
  await open(page);
  const textbox = page.getByRole("textbox", { name: "Sua pergunta sobre tecnologias Microsoft" });
  await expect(textbox).toBeVisible();
  await textbox.focus();
  await expect(page.locator(".composer-form")).toHaveCSS("border-top-color", "rgb(41, 151, 255)");
  const suggestion = page.locator(".suggestion").first();
  await suggestion.hover();
  await expect(suggestion).toHaveCSS("background-color", "rgb(24, 24, 27)");
  await page.getByRole("tab", { name: "Pergunte ao Eduardo", exact: true }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "O laboratório", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "Pergunte ao Eduardo", exact: true })).toBeFocused();
  const contrasts = await page.evaluate(() => {
    const luminance = (color) => color.match(/[\d.]+/g).slice(0, 3).map(Number)
      .map((value) => value / 255)
      .map((value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    return [".page-intro h1", ".page-intro p", ".profile-name", ".profile-description", ".suggestion", ".character-count", ".site-footer"]
      .map((selector) => {
        const node = document.querySelector(selector);
        let parent = node;
        while (parent && getComputedStyle(parent).backgroundColor === "rgba(0, 0, 0, 0)") parent = parent.parentElement;
        const foreground = luminance(getComputedStyle(node).color);
        const background = luminance(getComputedStyle(parent || document.body).backgroundColor);
        return (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05);
      });
  });
  expect(Math.min(...contrasts)).toBeGreaterThanOrEqual(4.5);
  expect(await page.locator(".suggestion").first().evaluate((node) => getComputedStyle(node).transitionDuration)).toBe("0s");
});
