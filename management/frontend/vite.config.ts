import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds to ../web/dist. The FastAPI management API serves that dist at / when present,
// and falls back to the interim vanilla-JS console (management/web/index.html) when it isn't —
// so a partial image (no node build) still boots an honest console. See DOCKER_OOTB_PLAN.md.
// Relative base so the built assets load regardless of the mount path.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../web/dist",
    emptyOutDir: true,
  },
  server: {
    // Dev proxy: `npm run dev` hits a locally running management API (no-DB uvicorn is fine —
    // v6 keeps an in-memory fallback when no DB is configured, per the #4407 contract lock).
    proxy: {
      "/api": "http://127.0.0.1:8090",
      "/health": "http://127.0.0.1:8090",
    },
  },
});
