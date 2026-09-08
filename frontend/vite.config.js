import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Renewals API (Horizon) — unchanged.
      "/api": "http://localhost:8000",
      // New Business API, dev-proxied at the SAME path prefix the production
      // gateway mounts it under ("/nb-app" -> the NewBusiness FastAPI app's own
      // "/api/*" routes). Frontend code never branches on dev vs. prod — it
      // always calls "/nb-app/api/...", and either this proxy or the gateway
      // makes that path resolve. See ForecastEngine/gateway/main.py.
      // rewrite strips "/nb-app" before forwarding, since the NB app's own
      // routes are plain "/api/*" — Starlette's app.mount() does this
      // automatically in the real gateway, but Vite's proxy needs it explicit.
      "/nb-app": {
        target: "http://localhost:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/nb-app/, ""),
      },
    },
  },
});
