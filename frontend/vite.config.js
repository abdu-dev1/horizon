import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Both prefixes go to the GATEWAY (`python gateway/run_app.py`), which is
    // the only thing the browser ever talks to in production too. The gateway
    // owns the /nb-app prefix stripping, identity resolution and the admin
    // policy, so proxying everything through it means dev exercises the same
    // request path as prod -- including auth. Pointing /nb-app straight at the
    // New Business engine instead would bypass all three.
    //
    // Because Vite forwards these server-side, the browser stays same-origin
    // and no CORS is involved in dev either -- which is why both backends have
    // no CORS middleware at all.
    proxy: {
      "/api": "http://localhost:8000",
      "/nb-app": "http://localhost:8000",
    },
  },
});
