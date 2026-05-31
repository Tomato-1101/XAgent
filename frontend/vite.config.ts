import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Hermes(5173)等と衝突しない専用ポート。strictPortで勝手にずれないようにする
  server: { port: 5180, strictPort: true },
});
