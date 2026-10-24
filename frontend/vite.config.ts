import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the backend runs on :8000; in the cluster the Traefik ingress routes /api to it.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 800 }, // recharts dominates the bundle; fine for an internal dashboard
  server: {
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET ?? "http://localhost:8000", ws: true, changeOrigin: true },
    },
  },
});
