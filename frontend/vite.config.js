import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During `npm run dev`, proxy API calls to the FastAPI backend on :8000,
// so the SPA can call /api/... with no CORS hassle.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
