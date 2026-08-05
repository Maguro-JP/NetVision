import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // バックエンドへは /api でプロキシする（CORS 設定なしでも開発できる）
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
  test: { globals: true, environment: "node" },
});
