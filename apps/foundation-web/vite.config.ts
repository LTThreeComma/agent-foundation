import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "");
  const apiTarget =
    environment.FOUNDATION_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

  return {
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "^/api(?:[/?]|$)": {
          target: apiTarget,
          changeOrigin: false,
        },
        "^/healthz/?(?:[?].*)?$": {
          target: apiTarget,
          changeOrigin: false,
        },
        "^/readyz/?(?:[?].*)?$": {
          target: apiTarget,
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
