import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  base: "/kb/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      input: "index.html",
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
        manualChunks(id) {
          if (id.includes("mathjax") || id.includes("math-render") || id.includes("mathjax-core")) {
            return "mathjax";
          }
          if (id.includes("marked")) return "marked";
        },
      },
    },
  },
  server: {
    port: 5175,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
