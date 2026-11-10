import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxy = {
    "/api": {
      target: env.SGZ_BACKEND_URL || "http://127.0.0.1:3080",
      // Preserve the browser's Host and Origin for the backend's same-origin check.
      changeOrigin: false,
    },
  };
  return {
    plugins: [react()],
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, proxy },
  };
});
