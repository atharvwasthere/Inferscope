import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Env vars are read Vite-native: import.meta.env.VITE_CHATBOT_URL /
// VITE_DASHBOARD_URL (see .env.example). No process.env shim.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: { host: "0.0.0.0", port: 3000 },
});
