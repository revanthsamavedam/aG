"""Provisioner — turn a foundry.yaml config into real Foundry resources.

Usage:
  python provision.py --config foundry.yaml --plan    # show the steps, change nothing
  python provision.py --config foundry.yaml --apply   # create/update everything
  python provision.py --config foundry.yaml --apply --only agents,routines

The provisioner talks to Foundry through the azure-ai-projects SDK
(AIProjectClient). It passes plain dicts — the SDK serialises them —
and every dict shape here was verified against the SDK's own models
(azure-ai-projects 2.11.0) with as_dict() wire captures:

  memory store       {"kind": "default", "chat_model": ...,
                      "embedding_model": ...,
                      "options": {"chat_summary_enabled": ...,
                                  "user_profile_enabled": ...}}
  agent definition   {"kind": "prompt", "model": ..., "instructions": ...,
                      "tools": [...]}
  web search tool    {"type": "web_search"}
  mcp tool           {"type": "mcp", "server_label": ..., "server_url": ...,
                      "allowed_tools": [...], "require_approval": ...}
  function tool      {"type": "function", "name": ..., "description": ...,
                      "parameters": {...}}
  openapi tool       {"type": "openapi", "openapi": {"spec": {...}}}
  memory search tool {"type": "memory_search_preview",
                      "memory_store_name": ..., "scope": "{{$userId}}",
                      "update_delay": 0}
  a2a tool           {"type": "a2a", "a2a_version": "1.0",
                      "project_connection_id": ...}
  schedule trigger   {"type": "schedule", "cron_expression": ...,
                      "time_zone": ...}
  timer trigger      {"type": "timer", "at": ...}
  teams trigger      {"type": "custom", "provider": "teams",
                      "event_name": "on_new_channel_message",
                      "parameters": {"connection_id": ..., "thread_type": "channel",
                                     "group_id": ..., "channel_id": ...}}
  github trigger     {"type": "github_issue", "connection_id": ...,
                      "owner": ..., "repository": ..., "issue_event": ...}
  routine action     {"type": "invoke_agent_responses_api",
                      "agent_name": ..., "input": ...}
  agent card         {"version": "1.0", "description": ...,
                      "skills": [{"id": ..., "name": ..., "description": ...}]}
  agent endpoint     {"protocol_configuration": {"responses": {}, "a2a": {}},
                      "authorization_schemes": [{"type": "Entra"}]}
  a2a connection     ARM PUT .../connections/{name}?api-version=2025-04-01-preview
                      {"properties": {"authType": "AgenticIdentityToken",
                                      "category": "RemoteA2A",
                                      "target": <a2a url>,
                                      "audience": "https://ai.azure.com"}}

Order of operations (dependencies first):
  1. memory store (agents' memory tools reference it by name)
  2. child agents, then — in a2a mode — A2A enablement + connections,
     then the parent (its A2A tools reference the connections)
  3. routines (they reference agents by name)
  4. the sequential workflow file (sequential mode only)

A2A enablement uses agents.update_details(agent_card=..., agent_endpoint=...)
— the SDK supports setting both in one call, so no raw REST is needed
for the agent side. The project connection (RemoteA2A) is an ARM
management-plane resource, so it goes through the HTTP helper with a
management.azure.com token. That call needs project.subscription_id
and project.resource_group in the config (a2a mode only).

Auth: the real client uses DefaultAzureCredential — run `az login`
first. No key is ever read from the config file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

from config import FoundryConfig, load_config

# Config tool kind -> wire `type` (the memory search tool's wire type
# is memory_search_preview, even though the config calls it memory_search).
WIRE_TYPE = {
    "web_search": "web_search",
    "mcp": "mcp",
    "function": "function",
    "openapi": "openapi",
    "memory_search": "memory_search_preview",
    "a2a": "a2a",
}

A2A_CONNECTION_API_VERSION = "2025-04-01-preview"
ROUTINE_ACTION_TYPE = "invoke_agent_responses_api"
MEMORY_SCOPE = "{{$userId}}"


# ---------------------------------------------------------------------------
# Dict builders (pure — no client calls, so tests can assert them directly)
# ---------------------------------------------------------------------------

def tool_dicts_for_agent(agent, config: FoundryConfig,
                         extra_tools: list[dict] | None = None) -> list[dict]:
    """The agent's tools as wire dicts, resolving mcp server labels."""
    out: list[dict] = []
    for tool in agent.tools:
        fields = dict(tool.fields)
        if tool.kind == "mcp":
            if "server" in fields:
                server = config.mcp_server(fields.pop("server"))
                fields.setdefault("server_label", server.label)
                fields.setdefault("server_url", server.url)
                if server.allowed_tools:
                    fields.setdefault("allowed_tools", list(server.allowed_tools))
                fields.setdefault("require_approval", server.require_approval)
                if server.headers:
                    fields.setdefault("headers", dict(server.headers))
        if tool.kind == "openapi" and "spec_file" in fields:
            spec_file = fields.pop("spec_file")
            with open(spec_file) as f:
                fields["openapi"] = {"spec": json.load(f)}
        out.append({"type": WIRE_TYPE[tool.kind], **fields})
    for extra in extra_tools or []:
        out.append(dict(extra))
    return out


def memory_tool_dict(store_name: str) -> dict:
    return {
        "type": "memory_search_preview",
        "memory_store_name": store_name,
        "scope": MEMORY_SCOPE,
        "update_delay": 0,
    }


def agent_definition(agent, config: FoundryConfig,
                     extra_tools: list[dict] | None = None) -> dict:
    tools = tool_dicts_for_agent(agent, config, extra_tools)
    if config.memory and agent.name in config.memory.attach_to:
        tools.append(memory_tool_dict(config.memory.store_name))
    return {
        "kind": "prompt",
        "model": agent.model or config.project.chat_model,
        "instructions": agent.instructions,
        "tools": tools,
    }


def agent_card_dict(agent) -> dict:
    """The A2A agent card, derived from the agent's instructions."""
    first_line = (agent.instructions.strip().splitlines() or [""])[0].strip()
    return {
        "version": "1.0",
        "description": first_line,
        "skills": [{
            "id": agent.name,
            "name": agent.name.replace("_", " ").title(),
            "description": first_line,
        }],
    }


def agent_endpoint_dict() -> dict:
    return {
        "protocol_configuration": {"responses": {}, "a2a": {}},
        "authorization_schemes": [{"type": "Entra"}],
    }


def parse_endpoint(endpoint: str) -> tuple[str, str]:
    """(account, project) from a Foundry project endpoint."""
    m = re.match(r"https://([^.]+)\.services\.ai\.azure\.com/api/projects/([^/]+)",
                 endpoint.rstrip("/"))
    if not m:
        raise ValueError(f"cannot parse project endpoint: {endpoint!r}")
    return m.group(1), m.group(2)


def a2a_url(endpoint: str, agent_name: str) -> str:
    account, project = parse_endpoint(endpoint)
    return (f"https://{account}.services.ai.azure.com/api/projects/"
            f"{project}/agents/{agent_name}/endpoint/protocols/a2a")


def a2a_connection_body(url: str) -> dict:
    return {
        "properties": {
            "authType": "AgenticIdentityToken",
            "category": "RemoteA2A",
            "target": url,
            "audience": "https://ai.azure.com",
        },
    }


def a2a_connection_url(endpoint: str, subscription_id: str,
                       resource_group: str, connection_name: str) -> str:
    account, project = parse_endpoint(endpoint)
    return (
        "https://management.azure.com/subscriptions/"
        f"{subscription_id}/resourceGroups/{resource_group}/providers/"
        f"Microsoft.CognitiveServices/accounts/{account}/projects/"
        f"{project}/connections/{connection_name}"
        f"?api-version={A2A_CONNECTION_API_VERSION}"
    )


def trigger_dict(trigger) -> dict:
    if trigger.kind == "schedule":
        return {"type": "schedule", "cron_expression": trigger.cron,
                "time_zone": trigger.time_zone}
    if trigger.kind == "timer":
        return {"type": "timer", "at": trigger.at}
    if trigger.kind == "teams":
        return {
            "type": "custom",
            "provider": "teams",
            "event_name": "on_new_channel_message",
            "parameters": {
                "connection_id": trigger.connection,
                "thread_type": "channel",
                "group_id": trigger.group_id,
                "channel_id": trigger.channel_id,
            },
        }
    if trigger.kind == "github_issue":
        return {"type": "github_issue", "connection_id": trigger.connection,
                "owner": trigger.owner, "repository": trigger.repository,
                "issue_event": trigger.issue_event}
    raise ValueError(f"unknown trigger kind: {trigger.kind!r}")


def action_dict(routine) -> dict:
    return {"type": ROUTINE_ACTION_TYPE, "agent_name": routine.agent,
            "input": routine.prompt}


def memory_store_definition(memory) -> dict:
    """The memory store definition — the wire shape of
    MemoryStoreDefaultDefinition (azure-ai-projects 2.11.0, verified
    with as_dict()): kind "default", chat + embedding model, and the
    two enable flags flat under options."""
    return {
        "kind": "default",
        "chat_model": memory.chat_model,
        "embedding_model": memory.embedding_model,
        "options": {
            "chat_summary_enabled": memory.chat_summary,
            "user_profile_enabled": memory.user_profile,
        },
    }


# ---------------------------------------------------------------------------
# HTTP helper (real: urllib + Entra token; fake: records the call)
# ---------------------------------------------------------------------------

class AzureHttp:
    """Raw HTTP with an Entra bearer token, for management-plane calls
    the SDK does not expose (the ARM connection PUT)."""

    def __init__(self, credential):
        self.credential = credential

    def request(self, method, url, body, scope):
        token = self.credential.get_token(scope).token
        req = urllib.request.Request(
            url, method=method, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            payload = resp.read() or b"{}"
            return {"status": resp.status, "body": json.loads(payload)}


# ---------------------------------------------------------------------------
# Provisioner
# ---------------------------------------------------------------------------

class FoundryProvisioner:
    """Applies a FoundryConfig to a project client (real or fake).

    `client` is any object with the AIProjectClient shape the fake
    mirrors: client.agents.create_version/get/update_details,
    client.beta.memory_stores.get/create,
    client.beta.routines.create_or_update, and client.http.request
    (or an explicit `http` passed here)."""

    def __init__(self, client, endpoint: str, http=None, print_fn=print):
        self.client = client
        self.endpoint = endpoint
        self.http = http if http is not None else getattr(client, "http", None)
        self.print = print_fn
        self.done: list[dict] = []
        self.agent_versions: dict[str, str] = {}

    # -- ordering ------------------------------------------------------
    def _a2a_tools(self, config: FoundryConfig) -> list[dict]:
        return [{
            "type": "a2a",
            "a2a_version": "1.0",
            "project_connection_id": f"{child}-a2a",
        } for child in config.orchestration.children]

    # -- plan ----------------------------------------------------------
    def _agent_step(self, agent, config: FoundryConfig,
                    extra_tools: list[dict] | None = None) -> dict:
        definition = agent_definition(agent, config, extra_tools)
        return {"kind": "agent", "name": agent.name,
                "action": "create_version",
                "detail": f"model={definition['model']} "
                          f"tools={[t['type'] for t in definition['tools']]}",
                "definition": definition}

    def plan(self, config: FoundryConfig) -> list[dict]:
        """The steps apply() would run, without calling anything.
        Order is dependency order: memory store, then children, then
        (a2a mode) A2A enablement + connections, then the parent —
        the parent's A2A tools reference the connections, so it is
        created last."""
        steps: list[dict] = []
        if config.memory:
            steps.append({"kind": "memory_store", "name": config.memory.store_name,
                          "action": "ensure",
                          "detail": f"chat={config.memory.chat_model} "
                                    f"embedding={config.memory.embedding_model}"})
        if config.orchestration and config.orchestration.mode == "a2a":
            children = [a for a in config.agents
                        if a.name in config.orchestration.children]
            rest = [a for a in config.agents
                    if a.name not in config.orchestration.children]
            for agent in children:
                steps.append(self._agent_step(agent, config))
            for child in config.orchestration.children:
                steps.append({"kind": "a2a_enable", "name": child,
                              "action": "update_details",
                              "detail": "agent_card + a2a protocol, Entra auth"})
                steps.append({"kind": "a2a_connection", "name": f"{child}-a2a",
                              "action": "arm_put",
                              "detail": f"target={a2a_url(self.endpoint, child)}"})
            for agent in rest:
                extra = (self._a2a_tools(config)
                         if agent.name == config.orchestration.parent else None)
                steps.append(self._agent_step(agent, config, extra))
        else:
            for agent in config.agents:
                steps.append(self._agent_step(agent, config))
        for routine in config.routines:
            steps.append({"kind": "routine", "name": routine.name,
                          "action": "create_or_update",
                          "detail": f"agent={routine.agent} "
                                    f"trigger={routine.trigger.kind} "
                                    f"enabled={routine.enabled}"})
        if config.orchestration and config.orchestration.mode == "sequential":
            steps.append({"kind": "workflow", "name": "workflow.py",
                          "action": "generate",
                          "detail": "steps=" + ",".join(config.orchestration.steps)})
        return steps

    # -- apply ---------------------------------------------------------
    def apply(self, config: FoundryConfig, dry_run: bool = False,
              only: set[str] | None = None, out_dir: str | None = None
              ) -> list[dict]:
        """Run the plan. dry_run prints each step and changes nothing.
        `only` limits the kinds run (e.g. {"agents", "routines"})."""
        self.done = []
        for step in self.plan(config):
            if only and step["kind"] not in only:
                continue
            self._run_step(step, config, dry_run, out_dir)
        return self.done

    def _run_step(self, step, config, dry_run, out_dir):
        kind = step["kind"]
        label = f"[{kind}] {step['name']}: {step['action']} ({step['detail']})"
        if dry_run:
            self.print(f"  would {label}")
            self.done.append({**step, "done": False})
            return
        self.print(f"  {label}")
        if kind == "memory_store":
            self._ensure_memory_store(config.memory)
        elif kind == "agent":
            result = self.client.agents.create_version(
                agent_name=step["name"], definition=step["definition"])
            # The workflow file needs the version (FoundryAgent requires
            # agent_version for prompt agents). "1" when the client does
            # not report one.
            self.agent_versions[step["name"]] = str(
                getattr(result, "version", "1") or "1")
        elif kind == "a2a_enable":
            agent = config.agent(step["name"])
            self.client.agents.update_details(
                agent_name=step["name"],
                agent_card=agent_card_dict(agent),
                agent_endpoint=agent_endpoint_dict())
        elif kind == "a2a_connection":
            self._create_a2a_connection(config, step["name"])
        elif kind == "routine":
            routine = next(r for r in config.routines if r.name == step["name"])
            self.client.beta.routines.create_or_update(
                routine_name=routine.name,
                description=routine.description or None,
                enabled=routine.enabled,
                triggers={"trigger": trigger_dict(routine.trigger)},
                action=action_dict(routine),
                authorization={"identity": {"type": "project"}})
        elif kind == "workflow":
            from workflow_gen import generate_workflow
            path = generate_workflow(config, out_dir or ".",
                                     versions=self.agent_versions)
            self.print(f"    wrote {path}")
        self.done.append({**step, "done": True})

    def _ensure_memory_store(self, memory) -> None:
        try:
            self.client.beta.memory_stores.get(memory.store_name)
            self.print(f"    memory store {memory.store_name!r} already exists")
            return
        except LookupError:
            pass
        self.client.beta.memory_stores.create(
            name=memory.store_name,
            definition=memory_store_definition(memory))

    def _create_a2a_connection(self, config, connection_name: str) -> None:
        if not config.project.subscription_id or not config.project.resource_group:
            raise ValueError(
                "a2a mode needs project.subscription_id and "
                "project.resource_group in the config (the ARM connection "
                "PUT is a management-plane call)")
        child = connection_name[:-len("-a2a")]
        url = a2a_connection_url(
            self.endpoint, config.project.subscription_id,
            config.project.resource_group, connection_name)
        self.http.request(
            "PUT", url, a2a_connection_body(a2a_url(self.endpoint, child)),
            scope="https://management.azure.com/.default")


# ---------------------------------------------------------------------------
# Real client + CLI
# ---------------------------------------------------------------------------

def connect_from_env() -> FoundryProvisioner:
    """AIProjectClient from FOUNDRY_PROJECT_ENDPOINT + DefaultAzureCredential."""
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise SystemExit(
            "set FOUNDRY_PROJECT_ENDPOINT to your project's endpoint "
            "(portal: your project -> Overview -> Endpoint), and run "
            "`az login` first")
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        raise SystemExit(
            "the Foundry SDK is not installed in this Python. Run:\n"
            "  pip install \"azure-ai-projects>=2.4.0\" azure-identity")
    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=endpoint, credential=credential)
    return FoundryProvisioner(client, endpoint, http=AzureHttp(credential))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Provision Foundry resources from a foundry.yaml config.")
    parser.add_argument("--config", default="foundry.yaml")
    parser.add_argument("--plan", action="store_true",
                        help="show the steps, change nothing (default)")
    parser.add_argument("--apply", action="store_true",
                        help="create/update the resources")
    parser.add_argument("--only", default="",
                        help="comma-separated kinds to run: "
                             "memory_store,agent,a2a_enable,a2a_connection,"
                             "routine,workflow")
    parser.add_argument("--out-dir", default=".",
                        help="where the generated workflow.py goes")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    provisioner = connect_from_env()
    only = {k.strip() for k in args.only.split(",") if k.strip()} or None

    if args.apply:
        print(f"Applying {args.config} to {config.project.endpoint} ...")
        done = provisioner.apply(config, dry_run=False, only=only,
                                 out_dir=args.out_dir)
        print(f"Done: {len([d for d in done if d['done']])} steps applied.")
    else:
        print(f"Plan for {args.config} (nothing will change):")
        provisioner.apply(config, dry_run=True, only=only,
                          out_dir=args.out_dir)
        print("Run with --apply to do it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
