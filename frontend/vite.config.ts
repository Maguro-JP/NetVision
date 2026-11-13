import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // 変わりにくい部品を画面の本体から分ける。1 つの塊が大きいと、開くたびに全部を読み直すことになる
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          query: ["@tanstack/react-query"],
          flow: ["@xyflow/react"],
        },
      },
    },
  },
  server: {
    port: 5173,
    // バックエンドへは /api でプロキシする（CORS 設定なしでも開発できる）
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
  test: {
    globals: true,
    environment: "node",
    // 時刻の表示を確かめるテストがあるので、動かす場所で結果が変わらないよう固定する。
    // 使う人が日本にいる前提のソフトなので、そこに合わせておく
    env: { TZ: "Asia/Tokyo" },
  },
});
