# Manual setup — do it once by hand, in the portal

Do these steps in order. Each one builds on the last. At the end of
each step there is a **check** — do not move on until it passes.

You need: an Azure subscription, and the **Foundry User** role on the
project (or a project where you are the owner). If a page below has
moved, search the portal for the feature name (e.g. "Routines") —
Microsoft renames portal pages often, and the names here were checked
against Microsoft Learn in November 2026.

The matching config-as-code version of each step is in
`foundry.yaml`; the provisioner (`provision.py`) does steps 2–6 from
that file. The portal steps below are the same operations, by hand.

---

## Step 0 — Sign in and open Foundry

1. Go to <https://ai.azure.com/> and sign in with your Azure account.
2. If you already have a project, open it. If not, do step 1.

**Check:** you see your project's Overview page.

---

## Step 1 — Create a Foundry project and deploy a model

**Why:** everything else lives inside a project, and every agent
needs a model deployment to think with.

1. In the Foundry portal, select **+ New project** (or **Create
   project**).
2. Give it a name (e.g. `tutorial-team`), pick a subscription and a
   region, and create it. (A hub is created for you if you do not
   have one.)
3. In your new project, go to **Models + endpoints** (left nav) →
   **Deploy model** → **Deploy base model**.
4. Pick a chat model — the quickstarts use `gpt-5-mini` — and deploy
   it. Note the **deployment name** (it is often the same as the
   model name). This is `project.chat_model` in the config.
5. Deploy an embedding model too (e.g. `text-embedding-3-large`).
   Memory needs it in step 3. This is `project.embedding_model`.

**Check:** Models + endpoints lists both deployments with status
Succeeded. On the project Overview page, copy the **Endpoint** —
it looks like
`https://<account>.services.ai.azure.com/api/projects/<project>`.
That is `project.endpoint` in the config, and the value of the
`FOUNDRY_PROJECT_ENDPOINT` environment variable for the provisioner.

*Config equivalent:* the `project:` section of `foundry.yaml`.
*Docs:* "Quickstart: Set up Microsoft Foundry resources" (Learn).

---

## Step 2 — Create and configure an agent

**Why:** an agent is a named set of instructions + a model + tools.
The five specialists are five agents; the Leader will be a sixth.

1. In your project, go to **Build** (left nav) → **Agents** →
   **+ New agent** (or **Create agent**).
2. Enter a **Name** — lowercase, no spaces, e.g. `researcher`.
   This is the agent's name in the API; the provisioner uses it as
   the `agent_name`.
3. Pick the **Model deployment** from step 1.
4. Write **Instructions**: one or two paragraphs saying what the
   agent does and how it should behave. Example for the researcher:

   > You are the Researcher. Find and compare options, cite sources,
   > and say plainly when you could not verify a claim. Never invent
   > a fact, a link, or a version number.

5. Save / create the agent. Repeat for `dev`, `qa`, `writer`,
   `reviewer`, and `leader`, using the instructions in
   `foundry.yaml` (the `agents:` section).

**Check:** open an agent and use its **Playground** (the chat pane)
to ask it one question in its specialty. It should answer in
character. If it answers as a generic chatbot, the instructions did
not save — re-open the agent and check the Instructions field.

*Config equivalent:* the `agents:` section; the provisioner calls
`agents.create_version` for each one.
*Docs:* "Create a prompt agent" (Learn).

---

## Step 3 — Attach the Memory Service (per user)

**Why:** without memory, every conversation starts from zero. With
it, an agent remembers a user's preferences and earlier context
across sessions. The scope is the **user id**: a user only ever sees
their own memories.

1. Open an agent (start with `leader`).
2. Find the **Memory** section on the agent page and enable it —
   the portal enables memory with a single click. A memory store is
   automatically created and configured for the agent.
3. The agent now has a **memory search tool** attached. That tool
   searches the store with `scope` set to the user id, so each user's
   memories stay separate.
4. Repeat for `writer` (the other agent that needs memory in the
   example config).

**Check:** in the agent's Playground, tell it a preference ("I like
summaries as bullet points, no more than five"). Wait a few seconds
(the store updates after a short delay), start a **new**
conversation with the same signed-in user, and ask for a summary.
It should come back as short bullets. If it does not, the store has
not updated yet — wait a minute and try again.

**Notes:**

- Memory is in **public preview**. There is no portal page that
  lists memory stores — inspect them through the SDK only
  (`client.beta.memory_stores`).
- The store needs the chat model and the embedding model from step 1.
- To remember a user across sessions, the caller must pass the same
  user id every time (`scope: {{$userId}}` in the tool).

*Config equivalent:* the `memory:` section; the provisioner creates
the store (`beta.memory_stores.create`) and adds the memory search
tool to the agents in `attach_to`.
*Docs:* "Create and use memory in Foundry Agent Service" (Learn);
"Introducing Memory in Foundry Agent Service" (Foundry blog, Ignite
2025).

---

## Step 4 — Create a Routine (schedule)

**Why:** a Routine is a schedule that lives in Foundry. Foundry
wakes the agent on the trigger — there is no cron job on your
machine, and the schedule is not in Teams.

1. In your project, select **Routines** in the left nav.
2. Select **+ New routine**.
3. Enter a **Name**, e.g. `daily-standup`.
4. Select an **Agent** from the dropdown — `leader`.
5. Enter a **Prompt**: what the agent runs each time, e.g.
   "Summarise what the team finished yesterday and what is next,
   from memory."
6. Under **Trigger**, set **Type** to **Recurring schedule**, then
   choose a **Frequency** (**Daily** or **Weekly**) and a **Time**.
   (The portal reads the time in your browser's time zone. For a
   weekday-only schedule with a pinned time zone, use the config
   instead — the portal only offers Daily/Weekly.)
7. Select **Create & start**.

**Check:** open the routine and select **Test run**, then inspect
its past-runs table. A run should appear with the agent's summary.
If no run appears, check that the routine is enabled and that the
agent name is right.

**Rules:** a routine has exactly one trigger and one action. The
minimum interval for a recurring schedule is **5 minutes**. The
action is always "invoke this agent with this input".

*Config equivalent:* the `routines:` section; the provisioner calls
`beta.routines.create_or_update` with a schedule trigger
(`cron_expression` + `time_zone`) and an agent action.
*Docs:* "Automate agents with routines" (Learn); Routines are GA
since September 24, 2026.

---

## Step 5 — Add a Teams channel-message trigger

**Why:** a message in a Teams channel can wake an agent. Teams is
the *event*; Foundry is still the scheduler — the routine lives in
Foundry, and the incoming message becomes the agent's input.

There are two parts, and the first one needs a human in a browser.

### 5a — Create the Teams connector connection (once)

1. In your project, go to **Build** → **Agents** → open any agent →
   **Tools** (or **Management center** → **Connected resources**,
   depending on the portal layout) and create a new connection of
   type **Teams** (the Microsoft Teams connector).
2. Give it a name, e.g. `teams-connection`. This is the
   `connection_id` the routine trigger references.
3. The portal will show an **OAuth consent link**. Open it in your
   browser and sign in with the Teams account that should receive
   the messages. Until you consent, the connection cannot read the
   channel.
4. Note the connection name — the provisioner cannot create this
   connection for you, because the consent step needs a human.

### 5b — Create the routine

1. **Routines** → **+ New routine**.
2. Name it, e.g. `teams-channel-summary`. Select the **Agent** —
   `writer`.
3. Under **Trigger**, set **Type** to **Teams** (a custom/event
   trigger). Pick the connection from 5a, then the **team**
   (group id) and **channel** (channel id).
4. No prompt is needed: the incoming message becomes the agent's
   input. Select **Create & start**.

**Check:** post a message in that Teams channel. A run should appear
in the routine's past-runs table, and the agent's reply should be
about your message. If nothing runs, check the connection's consent
(step 5a) and that the team/channel ids are the right ones.

**How to find a team/channel id:** in Teams, open the channel, use
the "…" menu → **Get link to channel** — the ids are in the link.
Or ask your Teams admin. The config wants them in the form
`19:<id>@thread.tacv2`.

*Config equivalent:* a routine whose trigger is
`type: teams` with `connection`, `group_id`, and `channel_id`. The
provisioner creates the routine, not the connection.
*Docs:* "Automate agents with routines" (Learn), trigger types
table (schedule / timer / github_issue / custom Teams).

---

## Step 6 — Connect MCP tools to an agent

**Why:** MCP (Model Context Protocol) lets an agent use tools from a
remote server — e.g. a Playwright server that can drive a browser.
The agent calls the server over HTTP; the server runs the tool.

1. Open an agent (e.g. `dev`) → **Playground** → **Tools** → **Add**
   → **Custom** → **MCP** → **Create**.
2. Enter the **server URL** — the remote MCP server's endpoint,
   e.g. `https://your-playwright-mcp.example.com/mcp`.
3. Choose the **authentication**: a key (enter it as a header), the
   agent or project identity (Entra), or OAuth passthrough.
4. Under **allowed tools**, pick which of the server's tools this
   agent may call (e.g. `browser_navigate`, `browser_snapshot`).
   Leave the rest off — an agent should only get the tools it needs.
5. Set **require approval**: `never` (the agent calls freely),
   `always` (a human approves each call), or per-tool.
6. Save.

**Check:** in the Playground, ask the agent to do something that
needs the MCP tool ("Open example.com and tell me its title"). The
tool call should appear in the run, and the answer should use what
the tool returned. If the tool never fires, check the server URL and
the auth — a wrong key fails silently as "tool not available".

*Config equivalent:* the `mcp_servers:` section (the server, by
label) plus a `type: mcp` tool on the agent. The provisioner does
not create the server — it is your endpoint — it just records where
it is and which tools the agent may call.
*Docs:* "Connect agents to MCP server endpoints" (Learn).

---

## Step 7 — Add tools: built-in web search, a function, an OpenAPI tool

Tools are added on the agent's **Tools** tab (or in the Playground's
Tools panel). The three kinds below cover the common cases.

### 7a — Built-in web search

1. On the agent, **Tools** → **Add** → **Web search** (built-in).
2. Save. No extra resource is needed — there is no separate Bing
   resource to create.

**Check:** ask the agent a question about something recent ("What
happened in the news today?"). The answer should cite live results.

### 7b — A custom function tool

A function tool declares a schema (name, description, parameters).
**Foundry declares the tool; your code implements it** — when the
agent is invoked through the SDK, your app receives the function
call and runs it.

1. On the agent, **Tools** → **Add** → **Custom function**.
2. Enter the **name** (e.g. `lookup_issue`), a **description**, and
   the **parameters** as a JSON schema, e.g.:

   ```json
   {"type": "object",
    "properties": {"number": {"type": "integer"}},
    "required": ["number"]}
   ```

3. Save. In the app that calls the agent, implement a function with
   the same name and parameters (see the Agent Framework workflow in
   `workflow_gen.py` for where function implementations live).

### 7c — An OpenAPI tool

1. On the agent, **Tools** → **Add** → **OpenAPI**.
2. Paste the OpenAPI spec (or a URL to it). The agent can then call
   the API's operations as tools.
3. Save.

**Check:** ask the agent to use the tool ("Search our docs for
'standup'"). It should call the API and answer from the result.

*Config equivalent:* tools on each agent in `foundry.yaml` —
`type: web_search`, `type: function` (with `name`, `description`,
`parameters`), `type: openapi` (with an inline `spec` or a
`spec_file`).
*Docs:* "Create a prompt agent" (Learn), tools section.

---

## Step 8 — The parent/child team (what to do with all of this)

You now have six agents. The parent/child piece is how they work as
a team, and there are two ways to wire it. You only need one.

### Option A — Sequential workflow (recommended)

The Leader runs the children in a fixed order. This is code, not a
portal setting:

1. Install the packages: `pip install agent-framework
   agent-framework-foundry azure-identity`.
2. The provisioner generates the workflow from your config:
   `python provision.py --config foundry.yaml --apply` writes
   `workflow.py` (steps from `orchestration.steps`).
3. Run it: `python workflow.py "Build me a FastAPI service that
   returns a random quote about NVIDIA."` The workflow runs the
   researcher, then the dev, then the writer, in order, and prints
   the integrated result.

### Option B — A2A (the Leader calls the children as a Foundry agent)

1. For **each child** agent, enable incoming A2A: this is a REST/SDK
   call, not a portal setting. The provisioner does it with
   `agents.update_details` (agent card + A2A protocol, Entra auth).
2. Create a **project connection** per child (ARM, type `RemoteA2A`,
   auth `AgenticIdentityToken`, target = the child's A2A URL). The
   provisioner does this from `project.subscription_id` +
   `project.resource_group`.
3. The Leader gets one **A2A tool** per child. It then calls the
   children itself, in whatever order the model chooses.

Set `orchestration.mode` to `sequential` or `a2a` in the config to
pick. The README explains why sequential is the default.
