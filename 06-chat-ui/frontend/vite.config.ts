import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server. The chat UI talks DIRECTLY to the FastAPI AG-UI
// endpoint (VITE_AGENT_URL, default http://localhost:8000) through
// CopilotKit's HttpAgent. There is no Node/CopilotKit runtime in between:
// the browser calls the agent server, and the agent server must
// authenticate and authorize every request itself (see README).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
