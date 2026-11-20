"""Fake Foundry client — the offline double for the provisioner.

Mirrors the slice of the azure-ai-projects surface the provisioner
touches, with plain dicts instead of SDK model objects:

  client.agents.create_version(agent_name, definition={...})
  client.agents.get(agent_name)                     -> raises LookupError
  client.agents.update_details(agent_name, agent_card=..., agent_endpoint=...)
  client.beta.memory_stores.get(name)               -> raises LookupError
  client.beta.memory_stores.create(name=..., definition={...})
  client.beta.routines.create_or_update(routine_name, triggers=..., action=..., ...)
  client.http.request(method, url, body, scope)     -> records raw calls

Every call is appended to ``client.calls`` (a list of dicts), so
tests can assert exactly what the provisioner asked for, and the
demo can print the plan it would have run against real Foundry.

The real SDK accepts the same calls with the same shapes — the
provisioner passes dicts, and the SDK serialises them (verified
against azure-ai-projects 2.11.0: create_version posts
{"definition": {...}} to /agents/{name}/versions?api-version=v1).
"""
from __future__ import annotations


class FakeAgent:
    def __init__(self, name: str, version: str = "1"):
        self.name = name
        self.version = version


class _FakeAgents:
    def __init__(self, client):
        self._client = client
        self.versions: dict[str, dict] = {}   # name -> latest definition
        self.details: dict[str, dict] = {}    # name -> update_details kwargs

    def create_version(self, agent_name, definition=None, **kwargs):
        definition = definition if definition is not None else kwargs.get("body", {})
        self._client.calls.append({
            "op": "agents.create_version", "agent_name": agent_name,
            "definition": definition,
        })
        self.versions[agent_name] = definition
        return FakeAgent(agent_name)

    def get(self, agent_name, **kwargs):
        if agent_name not in self.versions:
            raise LookupError(f"no agent named {agent_name!r}")
        return FakeAgent(agent_name)

    def update_details(self, agent_name, agent_card=None, agent_endpoint=None,
                       **kwargs):
        self._client.calls.append({
            "op": "agents.update_details", "agent_name": agent_name,
            "agent_card": agent_card, "agent_endpoint": agent_endpoint,
        })
        self.details[agent_name] = {
            "agent_card": agent_card, "agent_endpoint": agent_endpoint,
        }
        return FakeAgent(agent_name)


class _FakeMemoryStores:
    def __init__(self, client):
        self._client = client
        self.stores: dict[str, dict] = {}

    def get(self, name, **kwargs):
        if name not in self.stores:
            raise LookupError(f"no memory store named {name!r}")
        return {"name": name, **self.stores[name]}

    def create(self, name=None, definition=None, **kwargs):
        definition = definition if definition is not None else kwargs.get("body", {})
        self._client.calls.append({
            "op": "beta.memory_stores.create", "name": name,
            "definition": definition,
        })
        self.stores[name] = definition
        return {"name": name, **definition}


class _FakeRoutines:
    def __init__(self, client):
        self._client = client
        self.routines: dict[str, dict] = {}

    def create_or_update(self, routine_name, description=None, enabled=None,
                         triggers=None, action=None, authorization=None,
                         **kwargs):
        self._client.calls.append({
            "op": "beta.routines.create_or_update",
            "routine_name": routine_name, "description": description,
            "enabled": enabled, "triggers": triggers, "action": action,
            "authorization": authorization,
        })
        self.routines[routine_name] = {
            "description": description, "enabled": enabled,
            "triggers": triggers, "action": action,
        }
        return {"name": routine_name}


class _FakeBeta:
    def __init__(self, client):
        self.memory_stores = _FakeMemoryStores(client)
        self.routines = _FakeRoutines(client)


class _FakeHttp:
    """Records raw HTTP calls (ARM connection PUTs, etc.)."""
    def __init__(self, client):
        self._client = client

    def request(self, method, url, body, scope):
        self._client.calls.append({
            "op": "http.request", "method": method, "url": url,
            "body": body, "scope": scope,
        })
        return {"status": 200, "body": {}}


class FakeProjectClient:
    """Stand-in for AIProjectClient. `calls` is the audit trail."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.calls: list[dict] = []
        self.agents = _FakeAgents(self)
        self.beta = _FakeBeta(self)
        self.http = _FakeHttp(self)

    def call(self, op: str) -> list[dict]:
        """All recorded calls for one operation name."""
        return [c for c in self.calls if c["op"] == op]
