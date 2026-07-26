import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  base: "/studio/",
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
          if (id.includes("vis-network")) return "vis-network";
          // Lazy panels are already dynamic imports; avoid circular manualChunks.
          if (id.includes("EditionOps")) return "edition-ops";
          if (id.includes("CopilotPanel")) return "copilot";
          if (id.includes("node_modules")) {
            if (id.includes("@tanstack") || id.includes("zustand")) return "data-plane";
            if (id.includes("@tiptap") || id.includes("prosemirror")) return "tiptap";
            if (id.includes("marked")) return "marked";
          }
        },
      },
    },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
