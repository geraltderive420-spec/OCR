import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Développement : l'API FastAPI écoute sur 127.0.0.1:8000. Production : l'interface compilée est servie par FastAPI.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom", globals: true },
});
