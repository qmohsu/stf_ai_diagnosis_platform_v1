import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";
export default defineConfig({
  // Next.js requires `jsx: preserve`; Vitest 5/Vite must still lower TSX before import analysis.
  oxc: {
    jsx: {
      runtime: "automatic",
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    pool: "threads",
    maxWorkers: 1,
    fileParallelism: false,
    isolate: false,
    exclude: ["tests/e2e/**", "node_modules/**", ".next/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,tsx}"],
    },
  },
});
