import { defineConfig, devices } from "@playwright/test";

/**
 * Milestone 9's E2E suite (docs/testing-strategy.md's "E2E scenarios (Playwright)"),
 * run against the real compose stack (docker-compose.yml) -- nginx, two server
 * replicas, Redis and coturn -- not a dev server, so it exercises the exact same
 * artifact `docker compose up --build` produces. See e2e/README.md for how to run it
 * and why some Chromium launch flags are needed at all.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  // Scenarios create/join real rooms against a shared stack; running them one at a
  // time avoids one test's peer_joined racing another's inside the same server
  // process (nothing here is actually cross-test state, but it keeps failures legible).
  fullyParallel: false,
  workers: 1,
  // Real ICE negotiation timing varies run to run even on localhost (a documented,
  // accepted risk -- docs/architecture.md's risk table), not something a longer
  // timeout alone reliably absorbs. One retry, locally too, not just in CI -- and a
  // second one on CI specifically, whose shared runners are measurably slower and
  // less consistent for real-time networking than a local machine (observed: scenarios
  // that fail on attempt 1 at CI's full 30s connect timeout routinely succeed in a
  // few seconds on the very next attempt, which is retry absorbing hardware variance
  // working as intended, not masking a real bug).
  retries: process.env.CI ? 2 : 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:8080",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "docker compose -f ../docker-compose.yml up --build",
    url: "http://localhost:8080/health",
    reuseExistingServer: !process.env.CI,
    // A cold CI runner has no image-layer cache, so a from-scratch --build of both
    // the server and web images needs real headroom beyond a warm local rebuild.
    timeout: 300_000,
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          args: [
            // Deterministic localhost ICE (docs/testing-strategy.md): real host
            // candidates instead of Chromium's privacy mDNS obfuscation, which needs
            // a working local mDNS resolver that headless/CI hosts may not have.
            "--disable-features=WebRtcHideLocalIpsWithMdns",
            "--use-fake-ui-for-media-stream",
            // Only some sandboxed environments need this (no user-namespace support
            // for Chromium's own sandbox); harmless to add on a machine that doesn't.
            ...(process.env.PW_NO_SANDBOX ? ["--no-sandbox"] : []),
          ],
        },
      },
    },
  ],
});
