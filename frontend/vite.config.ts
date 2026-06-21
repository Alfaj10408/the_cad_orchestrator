import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy backend (8010) so the browser stays same-origin (no CORS needed).
// /api -> backend API, /viewer + /preview -> backend HTML/file routes.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    proxy: {
      "/api": { target: "http://localhost:8010", changeOrigin: true },
      "/viewer": { target: "http://localhost:8010", changeOrigin: true },
      "/preview": { target: "http://localhost:8010", changeOrigin: true },
    },
  },
});
