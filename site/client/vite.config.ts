import { defineConfig } from "vite";

// Vite default: build emits dist/, served by nginx:alpine in production.
// Dev server uses the upstream proxy hint so a local `npm run dev` can talk
// to a python agent running on localhost:10002 the same way prod talks to
// /agent via the public nginx vhost.
export default defineConfig({
  build: {
    target: "esnext",
    outDir: "dist",
  },
  resolve: {
    dedupe: ["lit"],
  },
  server: {
    proxy: {
      "/agent": {
        target: "http://localhost:10002",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/agent/, ""),
      },
    },
  },
});
