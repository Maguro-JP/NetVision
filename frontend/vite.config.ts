import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
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
