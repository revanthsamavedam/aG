"""Agent server: FastAPI + Microsoft Agent Framework, exposed over AG-UI.

The browser (CopilotKit HttpAgent) talks DIRECTLY to this server:
    POST http://localhost:8000/   (AG-UI RunAgentInput, SSE events back)

There is no Node/CopilotKit runtime in between. That means this server
must authenticate and authorize every request itself. THIS SCAFFOLD HAS
NO AUTH — it is for local learning only. Add auth before exposing it
(see README).

Modes (picked in this order):
  1. Foundry — FOUNDRY_PROJECT_ENDPOINT is set. Uses FoundryChatClient
     with DefaultAzureCredential (run `az login` first).
  2. OpenAI-compatible — OPENAI_API_KEY is set. Uses
     OpenAIChatCompletionClient with OPENAI_MODEL (default gpt-4o-mini)
     and optional OPENAI_BASE_URL.
  3. Demo — neither is set. Uses DemoChatClient (no model, no network),
     which can still call the show_risk_card tool from built-in data.

The agent has one tool, show_risk_card. Its JSON return shape is the
contract with the frontend's RiskCard component — keep both sides in
sync (see frontend/src/components/RiskCard.tsx).
"""

from __future__ import annotations

import json
import os

from agent_framework import Agent
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from demo_client import DemoChatClient
from risk_data import lookup


def show_risk_card(company: str) -> str:
    """Show a risk card for a company.

    Args:
        company: The company name, e.g. "Anthropic".

    Returns:
        A JSON string: {"company", "risk_level", "summary",
        "risks": [{"title", "detail"}], "sources": [urls]}.
        The frontend renders it with the RiskCard component.
    """
    return json.dumps(lookup(company))


def build_chat_client():
    """Pick the chat client from the environment. Returns (client, mode)."""
    if os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        from azure.identity import DefaultAzureCredential

        from agent_framework.foundry import FoundryChatClient

        return (
            FoundryChatClient(
                project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                model=os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini"),
                credential=DefaultAzureCredential(),
            ),
            "foundry",
        )
    if os.environ.get("OPENAI_API_KEY"):
        from agent_framework.openai import OpenAIChatCompletionClient

        return (
            OpenAIChatCompletionClient(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                api_key=os.environ["OPENAI_API_KEY"],
                base_url=os.environ.get("OPENAI_BASE_URL"),
            ),
            "openai",
        )
    return DemoChatClient(), "demo"


chat_client, MODE = build_chat_client()

agent = Agent(
    chat_client,
    instructions=(
        "You are a research assistant. When the user asks about the risk "
        "on a company, call show_risk_card with the company name, then "
        "write one short paragraph summarising the card in plain text. "
        "For anything else, answer briefly in plain text."
    ),
    tools=[show_risk_card],
)

app = FastAPI(title="foundry-chat-ui agent server")

# CORS: the browser calls this server directly from the Vite dev server
# (http://localhost:5173), a different origin. In production, set
# CORS_ORIGINS to your frontend's exact origin — never "*" with auth.
origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "mode": MODE}


# The AG-UI endpoint: POST / takes RunAgentInput, streams AG-UI events.
# single_user=True: this scaffold has no auth and no per-user scoping, so
# it declares itself single-user. That is the honest setting for local
# learning. For multiple users, add real auth and an
# authorization_scope_resolver instead (see README).
add_agent_framework_fastapi_endpoint(app, agent, "/", single_user=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
