import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The app calls the backend via relative /api/* paths. In production nginx
// reverse-proxies those to the chatbot/dashboard services (see frontend/nginx.conf).
// In dev there is no nginx, so Vite proxies the same prefixes to the local services —
// keeping the frontend code identical in both worlds (no VITE_* URL env needed).
const proxyOpts = (target) => ({
  target,
  changeOrigin: true,
  // strip the /api/<svc> prefix so the backend sees /chat/stream, /dashboard/summary, etc.
});

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    host: "0.0.0.0",
    port: 3000,
    proxy: {
      "/api/chatbot": { ...proxyOpts("http://localhost:8082"), rewrite: (p) => p.replace(/^\/api\/chatbot/, "") },
      "/api/dashboard": { ...proxyOpts("http://localhost:8083"), rewrite: (p) => p.replace(/^\/api\/dashboard/, "") },
    },
  },
});
