import { defineConfig, loadEnv, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const dshTarget = env.DSH_BACKEND_URL || "http://127.0.0.1:3000";
  const proxy: Record<string, ProxyOptions> = {
    "/dsh": {
      target: dshTarget,
      changeOrigin: true,
      ws: true,
      rewrite: (path) => path.replace(/^\/dsh/, ""),
      configure(server) {
        // Same-origin local proxy; DSH still enforces its own cookie/token authentication.
        server.on("proxyReq", (req) =>
          req.setHeader("Origin", new URL(dshTarget).origin),
        );
        server.on("proxyReqWs", (req) =>
          req.setHeader("Origin", new URL(dshTarget).origin),
        );
      },
    },
    "/agent-api": {
      target: env.AGENT_BACKEND_URL || "http://127.0.0.1:8000",
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/agent-api/, ""),
    },
  };
  return {
    plugins: [react()],
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, proxy },
  };
});
