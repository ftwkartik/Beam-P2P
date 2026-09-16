import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    css: false,
    // e2e/ has its own runner (playwright.config.ts) and would otherwise match
    // vitest's default *.spec.ts glob too.
    exclude: [...configDefaults.exclude, "e2e/**"],
    coverage: {
      provider: "v8",
      // The engine (framing, hashing, resume) is where correctness matters most; see
      // docs/adr/007-react-typescript-client.md.
      include: ["src/engine/**"],
    },
  },
});
