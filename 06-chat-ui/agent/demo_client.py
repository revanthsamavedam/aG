"""Demo chat client: a fake model so the scaffold runs with no API key.

It does not call any model. It reads the last user message and:
  - if it names a company we have demo data for, it "calls" the
    show_risk_card tool (returns a function call the Agent executes);
  - if the tool already ran (a function_result is in the messages), it
    returns a short closing line;
  - otherwise it explains demo mode in plain text.

Why the layered base classes: in this framework the tool-calling loop
lives in the chat client, not in the Agent. A bare client that only
implements the get_response protocol gets ONE model turn and the Agent
never executes the tool. Composing BaseChatClient with
FunctionInvocationLayer gives the client the loop: model turn -> run the
tool -> model turn again. Real clients (OpenAI, Foundry) are built the
same way, so the demo behaves like the real thing.
"""

from __future__ import annotations

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)

from risk_data import RISK_DATA


def _message_text(message: Message) -> str:
    parts: list[str] = []
    for content in message.contents or []:
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, Content) and content.type == "text" and content.text:
            parts.append(content.text)
    return " ".join(parts)


def _has_function_result(messages) -> bool:
    for message in messages:
        for content in message.contents or []:
            if isinstance(content, Content) and content.type == "function_result":
                return True
    return False


def _last_user_text(messages) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return _message_text(message)
    return ""


def _mentioned_company(text: str) -> str | None:
    lowered = text.lower()
    for key, data in RISK_DATA.items():
        if key in lowered:
            return data["company"]
    return None


class DemoChatClient(FunctionInvocationLayer, BaseChatClient):
    """A chat client with no model behind it. Demo mode only."""

    def __init__(self) -> None:
        super().__init__()

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        # stream=True must return a ResponseStream (not a bare async
        # generator): the FunctionInvocationLayer uses it as an async
        # context manager while it runs the tool loop. The finalizer is
        # what turns the streamed updates back into one ChatResponse —
        # without it, get_final_response() returns the raw update list
        # and the layer crashes reading .usage_details.
        if stream:
            return ResponseStream(
                self._stream(messages),
                finalizer=lambda updates: ChatResponse.from_updates(updates),
            )
        return await self._respond(messages)

    async def _respond(self, messages) -> ChatResponse:
        updates = [u async for u in self._stream(messages)]
        return ChatResponse.from_updates(updates)

    async def _stream(self, messages):
        if _has_function_result(messages):
            yield ChatResponseUpdate(
                contents=[Content.from_text("Here is the risk summary — see the card above.")],
                role="assistant",
                finish_reason="stop",
                model="demo",
            )
            return

        company = _mentioned_company(_last_user_text(messages))
        if company:
            yield ChatResponseUpdate(
                contents=[
                    Content.from_function_call(
                        call_id="demo-call-1",
                        name="show_risk_card",
                        arguments={"company": company},
                    )
                ],
                role="assistant",
                finish_reason="tool_calls",
                model="demo",
            )
            return

        yield ChatResponseUpdate(
            contents=[
                Content.from_text(
                    "Demo mode, no model key set. Ask about a company I have "
                    "demo data for — try “What is the risk on Anthropic?” — "
                    "and I will show its risk card."
                )
            ],
            role="assistant",
            finish_reason="stop",
            model="demo",
        )
