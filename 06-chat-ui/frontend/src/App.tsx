import { HttpAgent } from "@ag-ui/client";
import { CopilotChat, CopilotKit } from "@copilotkit/react-core/v2";
import { useCustomComponents } from "./components/registry";

// The agent server URL is the ONLY config the browser needs. It comes from
// the environment at build time (VITE_AGENT_URL) and defaults to the local
// FastAPI dev server. The browser holds no credentials: the agent server
// must authenticate and authorize every request itself (see README).
const AGENT_URL =
  import.meta.env.VITE_AGENT_URL ?? "http://localhost:8000";

// One HttpAgent, created once. It speaks AG-UI straight to the FastAPI
// endpoint: POST RunAgentInput, SSE events back. No runtime in between.
const agent = new HttpAgent({ url: AGENT_URL });

function Chat() {
  // Registers the custom components (RiskCard, ...) with CopilotKit.
  // Must run inside the <CopilotKit> provider.
  useCustomComponents();

  return <CopilotChat agentId="default" />;
}

export default function App() {
  return (
    <CopilotKit selfManagedAgents={{ default: agent }}>
      <div className="app-shell">
        <header className="app-header">
          <h1>Foundry Chat</h1>
          <p>Try: “What is the risk on Anthropic?”</p>
        </header>
        <Chat />
      </div>
    </CopilotKit>
  );
}
