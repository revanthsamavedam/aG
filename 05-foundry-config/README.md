# 05 — Foundry config: manual first, then config as code

This folder takes the team from folders 01–04 and turns it into
something you can set up by hand **and** rebuild from one file.

- **01** gave you five separate Foundry agents and a Leader that was
  a plain model call — orchestration, not a hosted agent.
- **02** gave the agents memory (the managed Memory Service, per user).
- **03** gave them a schedule (Foundry Routines).
- **04** gave you a way to check the work.

This folder adds the two things that were missing:

1. **The whole setup, in order, by hand** — `MANUAL.md`. Do it once
   in the portal so you know what each piece is.
2. **The same setup, as one config file** — `foundry.yaml` plus the
   `provision.py` CLI. Run it again and you get the same setup, every
   time, with no clicking.
3. **The Leader as a real Foundry agent (the parent)** that delegates
   to the five specialists (the children) — the missing piece.

Read the manual first, then the config. The config only makes sense
once you have seen the pieces in the portal.

## The ideas, in plain language

**Parent and child agents.** A *child* is a specialist: it does one
job well (research, write code, test, write docs, review). A *parent*
is the agent that decides which child does what, in which order, and
combines their answers into one. In 01 the parent was a plain model
call in your code. Here the parent is a real Foundry agent too — it
lives in Foundry, has its own instructions, and delegates to the
children. The children do not know about each other; only the parent
knows the plan.

**A connection.** When one agent calls another agent, Foundry needs a
*connection*: a named record that says "this agent may call that
agent, at this address, with this identity". For the A2A pattern
below, each child gets a connection named `<child>-a2a`. The
connection is an Azure resource (an ARM resource), not a setting
inside the agent — that is why the provisioner needs your
subscription id and resource group for that mode, and why the
connection is created with a management-plane call.

**Why the schedule lives in Foundry, not in Teams.** A Routine is a
trigger plus an agent plus a prompt, and it lives in the Foundry
project. Teams is only the *event* that wakes it up: "a message
arrived in this channel". If the schedule lived in Teams, Teams
would have to know how to run your agent — it does not. Foundry
owns the schedule, the agent, and the run history; Teams just sends
the message that starts it. Same for a cron schedule: there is no
cron job on your machine. Foundry wakes the agent at 9am.

**Memory scope.** The memory store is per user: every memory is
tagged with the user id, and an agent only ever sees the memories of
the user it is talking to. That is the same rule as 02.

## The parent/child pattern we picked

**Primary: Microsoft Agent Framework, sequential workflow.**

The Leader is the parent workflow. It runs the children in a fixed
order — the `steps` list in the config — passing each child's output
to the next, then integrates. The provisioner generates a
`workflow.py` from the config that builds it:

```python
workflow = SequentialBuilder().participants([researcher, dev, writer]).build()
result = await workflow.run("Build me a FastAPI service ...")
```

Why this one:

- It is the **GA, Microsoft-recommended** orchestration for
  multi-agent teams (Agent Framework v1.0, April 3, 2026; Foundry
  hosts it). The Ignite 2025 blog "Introducing Multi-Agent Workflows
  in Foundry Agent Service" is the announcement.
- The order is **explicit and deterministic**: research, then build,
  then document. That is exactly the Leader's plan from 01
  (at most 3 ordered steps, dependencies first). A model-driven
  router can pick a different order each run; the workflow cannot.
- It runs in **plain Python** and is demoable offline with fakes,
  like everything else in this repo.
- The children are ordinary Foundry agents, created by the same
  provisioner. Nothing extra to deploy.

**Alternative: A2A (Agent-to-Agent protocol).**

The Leader is a Foundry prompt agent with one A2A tool per child.
Each child exposes an A2A endpoint (agent card + protocol config,
set with `agents.update_details`), and the parent reaches it through
a project connection (`RemoteA2A`, `AgenticIdentityToken`). The model
decides which child to call and in which order.

Why it is second, not first: more moving parts (agent cards,
connections, Entra roles), model-driven routing instead of an
explicit plan, and A2A v1.0 is JSONRPC-only, text-only, no
streaming. It is the right pick when the parent must choose the
child at run time from a large pool, or when the children live in
different projects. For a fixed team of five with a fixed order, the
workflow is simpler and more predictable.

The provisioner supports both: set `orchestration.mode` to
`sequential` or `a2a` in the config. The default is `sequential`.

**Not used: Connected Agents (classic).** The classic connected-agents
feature was removed from the new Foundry Agent Service (GA March 16,
2026) and the classic service retires March 31, 2027. Do not build
on it.

**Not used: Portal Workflows.** Foundry's portal Workflows feature
is retiring December 1, 2026. Microsoft's guidance is to use Agent
Framework workflows instead.

Sources: "Create a prompt agent", "Introducing Multi-Agent Workflows
in Foundry Agent Service" (Ignite 2025), "Enable incoming A2A on a
Foundry agent", "Connect to an A2A agent endpoint from Foundry Agent
Service", "Connect agents to MCP server endpoints", "Create and use
memory in Foundry Agent Service", "Automate agents with routines"
(all Microsoft Learn, checked November 2026).

## Files

| File | What it is |
| --- | --- |
| `MANUAL.md` | The portal steps, by hand, in order. Start here. |
| `foundry.yaml` | The example config: the five specialists + Leader, memory, two routines, one MCP server. |
| `config.py` | Loads and validates the YAML (PyYAML when installed, else a stdlib subset parser). |
| `provision.py` | The CLI: `--plan` shows the steps, `--apply` does them. |
| `workflow_gen.py` | Generates `workflow.py` (the sequential parent/child workflow) from the config. |
| `fake_foundry.py` | The offline fake client the tests and demo use. |
| `demo.py` | Loads the example config, plans it, applies it to the fake, prints everything. |
| `tests/test_config.py` | Tests. Offline, stdlib only. |

## The config, at a glance

```yaml
project:
  endpoint: "https://<account>.services.ai.azure.com/api/projects/<project>"
  chat_model: "gpt-5-mini"
  embedding_model: "text-embedding-3-large"

agents:            # one entry per agent: name, instructions, tools
  - name: researcher
    instructions: |
      You are the Researcher. ...
    tools:
      - type: web_search
      - type: mcp
        server: playwright

memory:            # one store, per user; attach_to lists who can search it
  store_name: "team-memory"
  attach_to: [leader, writer]

orchestration:     # the parent/child piece
  mode: sequential
  parent: leader
  children: [researcher, dev, qa, writer, reviewer]
  steps: [researcher, dev, writer]

routines:          # schedules that live in Foundry
  - name: daily-standup
    agent: leader
    prompt: "Summarise what the team finished yesterday ..."
    trigger:
      type: schedule
      cron: "0 9 * * 1-5"
      time_zone: "America/New_York"
  - name: teams-channel-summary
    agent: writer
    trigger:
      type: teams
      connection: "teams-connection"
      group_id: "19:your-team-id@thread.tacv2"
      channel_id: "19:your-channel-id@thread.tacv2"

mcp_servers:       # remote MCP servers, referenced by label
  - label: playwright
    url: "https://your-playwright-mcp.example.com/mcp"
    allowed_tools: [browser_navigate, browser_snapshot]
```

Rules the loader enforces (a bad config fails here, not halfway
through creating resources): agent names unique; the parent and every
child and step must be agents in the file; at most 3 steps (the
Leader's cap from 01); routine agents must exist; schedule routines
need a prompt and a 5-field cron with a minimum 5-minute interval;
Teams triggers need a connection, group id, and channel id; MCP tools
must reference a server in `mcp_servers`; a2a mode needs
`project.subscription_id` and `project.resource_group`.

## Run it

```bash
# Demo (offline, no Azure, no key):
python demo.py

# Tests (offline, stdlib only):
python -m unittest discover -s tests -v

# Against your real project (needs `az login` + FOUNDRY_PROJECT_ENDPOINT):
python provision.py --config foundry.yaml --plan
python provision.py --config foundry.yaml --apply
python provision.py --config foundry.yaml --apply --only agents,routines
```

The provisioner creates a new agent *version* each time it runs
(agents are versioned by name), and creates the memory store and
routines only if they do not already exist. Running it twice is safe.

## The YAML subset

If PyYAML is not installed, the loader uses a small stdlib parser
that handles exactly the shape this config uses: nested mappings,
lists of mappings, plain/quoted scalars, integers, booleans, `#`
comments, and `|` / `>` block scalars for multi-line instructions.
It does **not** handle anchors, flow style (`{}` / `[]`), tags, or
tabs for indentation. If your config needs full YAML, install PyYAML
(`pip install pyyaml`) and the loader uses it instead.

## What is preview, and what we could not verify

- **Memory is in public preview.** The Memory Service works, but
  the API and portal surface may change. The memory search tool's
  wire type is `memory_search_preview`.
- **Routines are GA** (September 24, 2026). The trigger and action
  dict shapes used here were verified against the SDK's own models
  (`ScheduleRoutineTrigger`, `CustomRoutineTrigger`,
  `InvokeAgentResponsesApiRoutineAction`) with `as_dict()` wire
  captures, not against a live service.
- **A2A enablement** uses `agents.update_details(agent_card=...,
  agent_endpoint=...)`, which the SDK exposes directly. The exact
  top-level nesting of the older REST PATCH body was not verified
  against a live service, so the provisioner uses the SDK call
  instead. Run `--plan` first and check the printed steps.
- **The Teams connector connection** (named `teams-connection` in the
  example) cannot be created by the provisioner: creating it needs a
  catalog lookup and an OAuth consent link in a browser, which is a
  human step. Create it first (MANUAL.md step 5), then run the
  provisioner. The routine itself is fully automated.
- **The Agent Framework workflow** (`workflow.py`) was verified
  against the installed packages by live import and source
  inspection: `agent-framework` 1.26.0,
  `agent-framework-foundry` 1.16.2,
  `agent-framework-orchestrations` 1.4.2. The key facts, all
  verified, not taken from a blog sample: `SequentialBuilder` lives
  in `agent_framework.orchestrations` (not top-level);
  `FoundryAgent(project_endpoint=..., agent_name=...,
  agent_version=...)` connects to a hosted agent by name and does
  not create agents; `workflow.run(task)` returns a result whose
  `get_outputs()` gives each participant's reply. The generated file
  was imported against those installed packages. It still needs a
  real project endpoint and a login to run; the demo only generates
  the file.
- **Portal click paths** in MANUAL.md were checked against
  Microsoft Learn in November 2026. Microsoft renames portal pages
  often — if a label has moved, search the portal for the feature
  name (e.g. "Routines") rather than the exact click path.
