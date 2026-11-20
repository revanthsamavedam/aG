"""Tests for 05-foundry-config. Offline, stdlib only, no Azure, no key.

Run from this folder:
  python -m unittest discover -s tests -v
"""
import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    ConfigError, YamlSubsetError, load_config, parse_config,
    parse_yaml_subset,
)
from fake_foundry import FakeProjectClient  # noqa: E402
from provision import (  # noqa: E402
    FoundryProvisioner, a2a_connection_body, a2a_connection_url, a2a_url,
    action_dict, agent_card_dict, agent_definition, agent_endpoint_dict,
    memory_store_definition, memory_tool_dict, trigger_dict,
)

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(os.path.dirname(HERE), "foundry.yaml")

MINIMAL_YAML = """
project:
  endpoint: "https://acct.services.ai.azure.com/api/projects/demo"
  chat_model: "gpt-5-mini"
  embedding_model: "text-embedding-3-large"
agents:
  - name: leader
    instructions: |
      You are the Leader.
      Plan at most 3 steps.
  - name: dev
    instructions: "You are Dev."
    tools:
      - type: web_search
memory:
  store_name: "team-memory"
  attach_to:
    - leader
orchestration:
  mode: sequential
  parent: leader
  children:
    - dev
  steps:
    - dev
routines:
  - name: daily-standup
    agent: leader
    prompt: "Summarise yesterday."
    trigger:
      type: schedule
      cron: "0 9 * * 1-5"
      time_zone: "America/New_York"
"""


def _minimal():
    from config import load_yaml_text
    return parse_config(load_yaml_text(MINIMAL_YAML))


class TestYamlSubset(unittest.TestCase):
    def test_nested_maps_lists_block_scalars(self):
        data = parse_yaml_subset(MINIMAL_YAML)
        self.assertEqual(data["project"]["chat_model"], "gpt-5-mini")
        self.assertEqual(data["agents"][0]["name"], "leader")
        self.assertEqual(data["agents"][0]["instructions"],
                         "You are the Leader.\nPlan at most 3 steps.\n")
        self.assertEqual(data["agents"][1]["tools"][0]["type"], "web_search")
        self.assertEqual(data["routines"][0]["trigger"]["cron"], "0 9 * * 1-5")

    def test_scalars(self):
        data = parse_yaml_subset(
            "a: 5\nb: true\nc: false\nd: null\ne: 'quoted # not comment'\n"
            "f: plain # comment\n")
        self.assertEqual(data, {"a": 5, "b": True, "c": False, "d": None,
                                "e": "quoted # not comment", "f": "plain"})

    def test_tabs_rejected(self):
        with self.assertRaises(YamlSubsetError):
            parse_yaml_subset("a:\n\tb: 1\n")

    def test_comment_before_first_list_item(self):
        # A comment inside a list must not make the block look like a
        # mapping (this ate the whole tools list in foundry.yaml).
        data = parse_yaml_subset(
            "agents:\n"
            "  - name: dev\n"
            "    tools:\n"
            "      # a comment above the first item\n"
            "      - type: web_search\n"
            "      # and one between items\n"
            "      - type: mcp\n"
            "        server: playwright\n")
        self.assertEqual(
            data["agents"][0]["tools"],
            [{"type": "web_search"},
             {"type": "mcp", "server": "playwright"}])


class TestConfigValidation(unittest.TestCase):
    def test_minimal_parses(self):
        cfg = _minimal()
        self.assertEqual(cfg.project.chat_model, "gpt-5-mini")
        self.assertEqual([a.name for a in cfg.agents], ["leader", "dev"])
        self.assertEqual(cfg.orchestration.mode, "sequential")

    def test_example_config_loads(self):
        cfg = load_config(EXAMPLE)
        self.assertEqual(len(cfg.agents), 6)
        self.assertEqual(cfg.orchestration.parent, "leader")
        self.assertEqual(len(cfg.orchestration.children), 5)
        self.assertEqual(cfg.routines[0].trigger.kind, "schedule")
        self.assertEqual(cfg.routines[1].trigger.kind, "teams")

    def test_bad_endpoint_rejected(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["project"]["endpoint"] = "https://example.com/nope"
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_duplicate_agent_rejected(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["agents"].append({"name": "dev", "instructions": "dup"})
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_unknown_orchestration_child_rejected(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["orchestration"]["children"] = ["ghost"]
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_step_must_be_a_child(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["orchestration"]["steps"] = ["leader"]
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_parent_cannot_be_its_own_child(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["orchestration"]["children"] = ["leader", "dev"]
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_too_many_steps_rejected(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["orchestration"]["children"] = ["dev", "qa", "writer", "reviewer"]
        data["agents"] += [{"name": n, "instructions": "x"}
                           for n in ("qa", "writer", "reviewer")]
        data["orchestration"]["steps"] = ["dev", "qa", "writer", "reviewer"]
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_routine_agent_must_exist(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["routines"][0]["agent"] = "ghost"
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_schedule_routine_needs_prompt(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["routines"][0]["prompt"] = ""
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_cron_min_interval(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["routines"][0]["trigger"]["cron"] = "*/1 * * * *"
        with self.assertRaises(ConfigError):
            parse_config(data)
        data["routines"][0]["trigger"]["cron"] = "*/5 * * * *"
        parse_config(data)  # 5 minutes is the minimum and is allowed

    def test_cron_needs_five_fields(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["routines"][0]["trigger"]["cron"] = "0 9 * *"
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_teams_trigger_needs_connection_and_ids(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["routines"][0]["trigger"] = {"type": "teams", "connection": "t"}
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_a2a_needs_subscription_and_resource_group(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["orchestration"]["mode"] = "a2a"
        with self.assertRaises(ConfigError):
            parse_config(data)
        data["project"]["subscription_id"] = "sub-1"
        data["project"]["resource_group"] = "rg-1"
        parse_config(data)

    def test_unknown_mcp_server_reference_rejected(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["agents"][1]["tools"] = [{"type": "mcp", "server": "nope"}]
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_unknown_tool_type_rejected(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["agents"][1]["tools"] = [{"type": "teleport"}]
        with self.assertRaises(ConfigError):
            parse_config(data)

    def test_memory_attach_target_must_exist(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["memory"]["attach_to"] = ["ghost"]
        with self.assertRaises(ConfigError):
            parse_config(data)


class TestWireDicts(unittest.TestCase):
    def test_agent_definition_memory_tool(self):
        cfg = _minimal()
        leader = cfg.agent("leader")
        definition = agent_definition(leader, cfg)
        self.assertEqual(definition["kind"], "prompt")
        self.assertEqual(definition["model"], "gpt-5-mini")
        self.assertEqual(definition["tools"], [memory_tool_dict("team-memory")])

    def test_memory_tool_wire_shape(self):
        self.assertEqual(memory_tool_dict("team-memory"), {
            "type": "memory_search_preview",
            "memory_store_name": "team-memory",
            "scope": "{{$userId}}",
            "update_delay": 0,
        })

    def test_mcp_server_label_resolves(self):
        cfg = _minimal()
        from config import McpServerSpec, ToolSpec
        cfg.mcp_servers = [McpServerSpec(
            label="playwright", url="https://x/mcp",
            allowed_tools=["browser_navigate"], require_approval="never")]
        dev = cfg.agent("dev")
        dev.tools = [ToolSpec(kind="mcp", fields={"server": "playwright"})]
        tools = agent_definition(dev, cfg)["tools"]
        self.assertEqual(tools[0], {
            "type": "mcp", "server_label": "playwright",
            "server_url": "https://x/mcp",
            "allowed_tools": ["browser_navigate"],
            "require_approval": "never",
        })

    def test_schedule_trigger_wire_shape(self):
        cfg = _minimal()
        self.assertEqual(trigger_dict(cfg.routines[0].trigger), {
            "type": "schedule", "cron_expression": "0 9 * * 1-5",
            "time_zone": "America/New_York",
        })

    def test_teams_trigger_wire_shape(self):
        from config import TriggerSpec
        t = TriggerSpec(kind="teams", connection="teams-connection",
                        group_id="g1", channel_id="c1")
        self.assertEqual(trigger_dict(t), {
            "type": "custom", "provider": "teams",
            "event_name": "on_new_channel_message",
            "parameters": {"connection_id": "teams-connection",
                           "thread_type": "channel",
                           "group_id": "g1", "channel_id": "c1"},
        })

    def test_routine_action_wire_shape(self):
        cfg = _minimal()
        self.assertEqual(action_dict(cfg.routines[0]), {
            "type": "invoke_agent_responses_api",
            "agent_name": "leader", "input": "Summarise yesterday.",
        })

    def test_agent_card_and_endpoint_wire_shapes(self):
        cfg = _minimal()
        card = agent_card_dict(cfg.agent("dev"))
        self.assertEqual(card["version"], "1.0")
        self.assertEqual(card["skills"][0]["id"], "dev")
        self.assertEqual(agent_endpoint_dict(), {
            "protocol_configuration": {"responses": {}, "a2a": {}},
            "authorization_schemes": [{"type": "Entra"}],
        })

    def test_a2a_urls_and_connection_body(self):
        endpoint = "https://acct.services.ai.azure.com/api/projects/demo"
        self.assertEqual(
            a2a_url(endpoint, "dev"),
            "https://acct.services.ai.azure.com/api/projects/demo"
            "/agents/dev/endpoint/protocols/a2a")
        self.assertEqual(
            a2a_connection_url(endpoint, "sub-1", "rg-1", "dev-a2a"),
            "https://management.azure.com/subscriptions/sub-1/resourceGroups/"
            "rg-1/providers/Microsoft.CognitiveServices/accounts/acct/"
            "projects/demo/connections/dev-a2a?api-version=2025-04-01-preview")
        self.assertEqual(a2a_connection_body("https://x/a2a"), {
            "properties": {
                "authType": "AgenticIdentityToken", "category": "RemoteA2A",
                "target": "https://x/a2a", "audience": "https://ai.azure.com",
            },
        })

    def test_memory_store_definition(self):
        cfg = _minimal()
        definition = memory_store_definition(cfg.memory)
        self.assertEqual(definition, {
            "kind": "default",
            "chat_model": "gpt-5-mini",
            "embedding_model": "text-embedding-3-large",
            "options": {"chat_summary_enabled": True,
                        "user_profile_enabled": True},
        })


class TestProvisionerFake(unittest.TestCase):
    def _run(self, cfg=None):
        cfg = cfg or _minimal()
        client = FakeProjectClient(cfg.project.endpoint)
        provisioner = FoundryProvisioner(client, cfg.project.endpoint)
        # Apply into a temp dir so the generated workflow.py never
        # lands in the repo folder.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return client, provisioner, provisioner.apply(
            cfg, out_dir=self._tmp.name)

    def test_sequential_apply_calls_fake_in_order(self):
        client, _, done = self._run()
        ops = [c["op"] for c in client.calls]
        self.assertEqual(ops, [
            "beta.memory_stores.create",
            "agents.create_version",   # leader
            "agents.create_version",   # dev
            "beta.routines.create_or_update",
        ])
        self.assertTrue(all(d["done"] for d in done))

    def test_memory_store_not_recreated_when_it_exists(self):
        cfg = _minimal()
        client = FakeProjectClient(cfg.project.endpoint)
        client.beta.memory_stores.stores["team-memory"] = {}
        with tempfile.TemporaryDirectory() as tmp:
            FoundryProvisioner(client, cfg.project.endpoint).apply(
                cfg, out_dir=tmp)
        self.assertEqual(client.call("beta.memory_stores.create"), [])

    def test_dry_run_changes_nothing(self):
        cfg = _minimal()
        client = FakeProjectClient(cfg.project.endpoint)
        provisioner = FoundryProvisioner(client, cfg.project.endpoint)
        done = provisioner.apply(cfg, dry_run=True)
        self.assertEqual(client.calls, [])
        self.assertTrue(all(not d["done"] for d in done))

    def test_only_limits_kinds(self):
        cfg = _minimal()
        client = FakeProjectClient(cfg.project.endpoint)
        with tempfile.TemporaryDirectory() as tmp:
            FoundryProvisioner(client, cfg.project.endpoint).apply(
                cfg, only={"routine"}, out_dir=tmp)
        self.assertEqual(
            [c["op"] for c in client.calls],
            ["beta.routines.create_or_update"])

    def test_routine_call_carries_trigger_and_action(self):
        client, _, _ = self._run()
        call = client.call("beta.routines.create_or_update")[0]
        self.assertEqual(call["routine_name"], "daily-standup")
        self.assertEqual(call["triggers"], {"trigger": {
            "type": "schedule", "cron_expression": "0 9 * * 1-5",
            "time_zone": "America/New_York"}})
        self.assertEqual(call["action"], {
            "type": "invoke_agent_responses_api",
            "agent_name": "leader", "input": "Summarise yesterday."})

    def test_a2a_mode_creates_children_then_parent_with_tools(self):
        from config import load_yaml_text
        data = load_yaml_text(MINIMAL_YAML)
        data["orchestration"]["mode"] = "a2a"
        data["project"]["subscription_id"] = "sub-1"
        data["project"]["resource_group"] = "rg-1"
        cfg = parse_config(data)
        client = FakeProjectClient(cfg.project.endpoint)
        with tempfile.TemporaryDirectory() as tmp:
            FoundryProvisioner(client, cfg.project.endpoint).apply(
                cfg, out_dir=tmp)
        ops = [c["op"] for c in client.calls]
        self.assertEqual(ops, [
            "beta.memory_stores.create",
            "agents.create_version",   # dev (child) first
            "agents.update_details",   # enable A2A on dev
            "http.request",            # ARM connection dev-a2a
            "agents.create_version",   # leader (parent) with A2A tool
            "beta.routines.create_or_update",
        ])
        parent = client.call("agents.create_version")[1]
        self.assertEqual(parent["agent_name"], "leader")
        self.assertIn({"type": "a2a", "a2a_version": "1.0",
                       "project_connection_id": "dev-a2a"},
                      parent["definition"]["tools"])
        http_call = client.call("http.request")[0]
        self.assertEqual(http_call["method"], "PUT")
        self.assertIn("/connections/dev-a2a", http_call["url"])
        self.assertEqual(http_call["body"]["properties"]["category"], "RemoteA2A")

    def test_example_config_applies_to_fake(self):
        cfg = load_config(EXAMPLE)
        client = FakeProjectClient(cfg.project.endpoint)
        with tempfile.TemporaryDirectory() as tmp:
            done = FoundryProvisioner(client, cfg.project.endpoint).apply(
                cfg, out_dir=tmp)
        kinds = [d["kind"] for d in done]
        self.assertEqual(kinds.count("agent"), 6)
        self.assertEqual(kinds.count("routine"), 2)
        self.assertIn("workflow", kinds)
        self.assertIn("memory_store", kinds)
        # The leader's definition carries the memory tool; the dev's
        # carries the resolved playwright MCP tool.
        versions = {c["agent_name"]: c["definition"]
                    for c in client.call("agents.create_version")}
        leader_tools = [t["type"] for t in versions["leader"]["tools"]]
        self.assertIn("memory_search_preview", leader_tools)
        dev_tools = versions["dev"]["tools"]
        self.assertIn("mcp", [t["type"] for t in dev_tools])
        mcp = next(t for t in dev_tools if t["type"] == "mcp")
        self.assertEqual(mcp["server_url"],
                         "https://your-playwright-mcp.example.com/mcp")


class TestWorkflowGen(unittest.TestCase):
    def test_generate_writes_valid_python(self):
        from workflow_gen import generate_workflow
        cfg = _minimal()
        with tempfile.TemporaryDirectory() as tmp:
            path = generate_workflow(cfg, tmp, versions={"dev": "3"})
            self.assertEqual(path, os.path.join(tmp, "workflow.py"))
            with open(path) as f:
                text = f.read()
        compile(text, "workflow.py", "exec")
        self.assertIn('agent_name="dev"', text)
        self.assertIn('agent_version="3"', text)
        self.assertIn("SequentialBuilder", text)
        self.assertIn("FoundryAgent", text)
        self.assertIn(cfg.project.endpoint, text)

    def test_generate_needs_sequential_mode(self):
        from config import load_yaml_text
        from workflow_gen import generate_workflow
        data = load_yaml_text(MINIMAL_YAML)
        data["orchestration"]["mode"] = "a2a"
        data["project"]["subscription_id"] = "sub-1"
        data["project"]["resource_group"] = "rg-1"
        cfg = parse_config(data)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                generate_workflow(cfg, tmp)

    def test_generated_workflow_lists_steps_in_order(self):
        from workflow_gen import generate_workflow
        cfg = load_config(EXAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            path = generate_workflow(cfg, tmp)
            with open(path) as f:
                text = f.read()
        positions = [text.index(f'agent_name="{s}"')
                     for s in cfg.orchestration.steps]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(cfg.orchestration.steps,
                         ["researcher", "dev", "writer"])


if __name__ == "__main__":
    unittest.main()
