import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  base: "/test/vite/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: "src/main.ts",
        math: "src/math-render.ts",
      },
      preserveEntrySignatures: "exports-only",
      output: {
        entryFileNames: "[name].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
