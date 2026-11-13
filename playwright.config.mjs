import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

export default defineConfig({
  testDir: fileURLToPath(new URL("./tests", import.meta.url)),
  testMatch: "browser.spec.mjs",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:18765",
    viewport: { width: 1440, height: 1100 },
    colorScheme: "light",
    reducedMotion: "reduce",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "python -m uvicorn backend.app:app --host 127.0.0.1 --port 18765 --no-proxy-headers --no-access-log",
    url: "http://127.0.0.1:18765",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
