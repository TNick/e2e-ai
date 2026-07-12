import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build is written straight into the Python package's bundled
// static assets, so `pip install e2e-ai[monitor]` ships the UI (no runtime Node).
// During `npm run dev`, /api is proxied to a locally running `e2e-ai ui` server.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/e2e_ai/monitor/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
