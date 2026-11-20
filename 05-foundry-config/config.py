"""foundry.yaml — load it, validate it, hand it to the provisioner.

The config is one YAML file that declares everything the tutorial
builds: the project, the agents and their tools, the memory store,
the parent/child orchestration, the routines, and the MCP servers.

YAML loading: PyYAML when it is installed (``pip install pyyaml``),
otherwise a small stdlib subset parser that handles exactly the
shape this config uses — nested mappings, lists of mappings,
plain/quoted scalars, ints, booleans, ``#`` comments, and ``|`` / ``>``
block scalars for multi-line instructions. The subset is documented
in README.md ("The YAML subset"). If your config uses YAML features
outside the subset (anchors, flow collections, multi-line quoted
strings), install PyYAML and the loader uses it instead.

Validation is strict on purpose: a bad config fails here, with a
message that names the field, instead of failing halfway through
creating resources in Foundry.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# YAML subset parser (stdlib fallback when PyYAML is not installed)
# ---------------------------------------------------------------------------
#
# Handles the subset this config uses. Indentation is 2 spaces per
# level. Supported: mappings, lists (including "- key: value" items),
# plain / single-quoted / double-quoted scalars, integers, booleans,
# null, "#" comments, and "|" / ">" block scalars. Not supported:
# anchors/aliases, flow style ({}/[]), tags, multi-line quoted
# strings, tabs for indentation.


class YamlSubsetError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    """Remove a trailing ' # comment' (not inside quotes)."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in " \t":
                return line[:i].rstrip()
    return line.rstrip()


def _parse_scalar(text: str):
    text = text.strip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if (len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'"):
        if text[0] == '"':
            return bytes(text[1:-1], "utf-8").decode("unicode_escape")
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_key(line: str):
    """Split 'key: value' at the first ': ' (or trailing ':').
    The key is unquoted like a scalar, so "200": becomes 200."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if i + 1 == len(line) or line[i + 1] == " ":
                return _parse_scalar(line[:i].strip()), line[i + 1:].strip()
    return None, None


def _parse_subset(lines: list[str], pos: int, indent: int):
    """Parse one block at `indent`. Returns (value, next_pos)."""
    # Skip blank/comment lines before deciding list vs map — a comment
    # inside a list (e.g. above the first "- item") must not make the
    # block look like a mapping.
    while pos < len(lines):
        stripped = lines[pos].strip()
        if not stripped or stripped.startswith("#"):
            pos += 1
            continue
        break
    if pos >= len(lines):
        return None, pos
    first = lines[pos]
    if first.lstrip().startswith("- ") or first.lstrip() == "-":
        return _parse_list(lines, pos, indent)
    return _parse_map(lines, pos, indent)


def _parse_list(lines: list[str], pos: int, indent: int):
    items: list = []
    while pos < len(lines):
        line = lines[pos]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            pos += 1
            continue
        if _indent_of(line) != indent or not stripped.startswith("-"):
            break
        after_dash = stripped[1:].lstrip()
        if not after_dash:
            # "-": nested block on the following lines.
            pos += 1
            value, pos = _parse_subset(lines, pos, indent + 2)
            items.append(value)
            continue
        key, value = _split_key(after_dash)
        if key is None:
            items.append(_parse_scalar(after_dash))
            pos += 1
            continue
        # "- key: value": a mapping whose first key is on the dash line.
        # The key sits at indent + 2, so a block scalar or nested block
        # under it is at indent + 4.
        item: dict = {}
        items.append(item)
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            block, pos = _read_block(lines, pos + 1, indent + 4, value)
            item[key] = block
        elif value:
            item[key] = _parse_scalar(value)
            pos += 1
        else:
            pos += 1
            nested, pos = _parse_subset(lines, pos, indent + 4)
            item[key] = nested
        # Remaining keys of this mapping, at dash+2 indent.
        while pos < len(lines):
            line = lines[pos]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                pos += 1
                continue
            if _indent_of(line) != indent + 2:
                break
            if stripped.startswith("-"):
                break
            k2, v2 = _split_key(stripped)
            if k2 is None:
                raise YamlSubsetError(f"cannot parse line: {line!r}")
            if v2 in ("|", ">", "|-", ">-", "|+", ">+"):
                block, pos = _read_block(lines, pos + 1, indent + 4, v2)
                item[k2] = block
            elif v2:
                item[k2] = _parse_scalar(v2)
                pos += 1
            else:
                pos += 1
                nested, pos = _parse_subset(lines, pos, indent + 4)
                item[k2] = nested
    return items, pos


def _parse_map(lines: list[str], pos: int, indent: int):
    mapping: dict = {}
    while pos < len(lines):
        line = lines[pos]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            pos += 1
            continue
        if _indent_of(line) != indent:
            break
        if stripped.startswith("-"):
            break
        key, value = _split_key(stripped)
        if key is None:
            raise YamlSubsetError(f"cannot parse line: {line!r}")
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            block, pos = _read_block(lines, pos + 1, indent + 2, value)
            mapping[key] = block
        elif value:
            mapping[key] = _parse_scalar(value)
            pos += 1
        else:
            pos += 1
            nested, pos = _parse_subset(lines, pos, indent + 2)
            mapping[key] = nested
    return mapping, pos


def _read_block(lines: list[str], pos: int, indent: int, marker: str):
    """Read a | or > block scalar. Returns (text, next_pos).

    Chomping follows YAML: `|` / `>` (clip) keep one trailing newline,
    `|-` / `>-` (strip) keep none, `|+` / `>+` (keep) keep them all.
    """
    collected: list[str] = []
    trailing_blanks = 0
    while pos < len(lines):
        line = lines[pos]
        if not line.strip():
            collected.append("")
            pos += 1
            continue
        if _indent_of(line) < indent:
            break
        collected.append(line[indent:])
        pos += 1
    # Count trailing blank lines (for the keep indicator).
    while collected and collected[-1] == "":
        collected.pop()
        trailing_blanks += 1
    chomp = marker[1] if len(marker) > 1 else ""
    if marker.startswith(">"):
        # Folded: single newlines become spaces, blank lines stay.
        out: list[str] = []
        for piece in collected:
            if piece == "":
                out.append("\n")
            elif out and not out[-1].endswith("\n"):
                out.append(" " + piece)
            else:
                out.append(piece)
        text = "".join(out)
    else:
        text = "\n".join(collected)
    if not text:
        return "", pos
    if chomp == "-":
        return text, pos
    if chomp == "+":
        return text + "\n" * (trailing_blanks + 1), pos
    return text + "\n", pos


def parse_yaml_subset(text: str):
    """Parse the YAML subset this config uses. Raises YamlSubsetError."""
    raw_lines = []
    for line in text.splitlines():
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise YamlSubsetError("tabs are not allowed for indentation: "
                                  f"{line!r}")
        raw_lines.append(_strip_comment(line))
    # Drop leading blank lines.
    pos = 0
    while pos < len(raw_lines) and not raw_lines[pos].strip():
        pos += 1
    if pos >= len(raw_lines):
        return {}
    value, _ = _parse_subset(raw_lines, pos, _indent_of(raw_lines[pos]))
    return value if value is not None else {}


def load_yaml_text(text: str):
    """Load YAML: PyYAML when installed, else the stdlib subset parser."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse_yaml_subset(text)
    data = yaml.safe_load(text)
    return data if data is not None else {}


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_CRON_RE = re.compile(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+$")
_MIN_INTERVAL_MINUTES = 5


class ConfigError(ValueError):
    """The config is invalid. The message names the field."""


@dataclass
class ToolSpec:
    """One tool on an agent. `kind` selects the shape; the rest of
    the mapping is the tool's own fields (kept as a dict so new tool
    types need no model change)."""
    kind: str
    fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """The tool as the Foundry API expects it (the `type` key
        plus the tool's fields)."""
        out = {"type": self.kind}
        out.update(self.fields)
        return out


@dataclass
class AgentSpec:
    name: str
    instructions: str
    model: str = ""
    tools: list[ToolSpec] = field(default_factory=list)


@dataclass
class MemorySpec:
    store_name: str
    chat_model: str
    embedding_model: str
    user_profile: bool = True
    chat_summary: bool = True
    attach_to: list[str] = field(default_factory=list)


@dataclass
class OrchestrationSpec:
    mode: str = "sequential"  # "sequential" | "a2a"
    parent: str = "leader"
    children: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


@dataclass
class TriggerSpec:
    kind: str  # "schedule" | "timer" | "teams" | "github_issue"
    cron: str = ""
    time_zone: str = ""
    at: str = ""
    connection: str = ""
    group_id: str = ""
    channel_id: str = ""
    owner: str = ""
    repository: str = ""
    issue_event: str = "opened"


@dataclass
class RoutineSpec:
    name: str
    agent: str
    trigger: TriggerSpec
    prompt: str = ""
    description: str = ""
    enabled: bool = True


@dataclass
class McpServerSpec:
    label: str
    url: str
    allowed_tools: list[str] = field(default_factory=list)
    require_approval: str = "never"
    headers: dict = field(default_factory=dict)


@dataclass
class ProjectSpec:
    endpoint: str
    chat_model: str
    embedding_model: str = ""
    # Only needed for orchestration.mode == "a2a": the A2A project
    # connections are ARM management-plane resources, so the
    # provisioner needs the subscription + resource group to address
    # them. Not needed for sequential mode.
    subscription_id: str = ""
    resource_group: str = ""


@dataclass
class FoundryConfig:
    project: ProjectSpec
    agents: list[AgentSpec] = field(default_factory=list)
    memory: MemorySpec | None = None
    orchestration: OrchestrationSpec | None = None
    routines: list[RoutineSpec] = field(default_factory=list)
    mcp_servers: list[McpServerSpec] = field(default_factory=list)

    # -- lookups ---------------------------------------------------------
    def agent_names(self) -> set[str]:
        return {a.name for a in self.agents}

    def agent(self, name: str) -> AgentSpec | None:
        for a in self.agents:
            if a.name == name:
                return a
        return None

    def mcp_server(self, label: str) -> McpServerSpec | None:
        for s in self.mcp_servers:
            if s.label == label:
                return s
        return None


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------

_TOOL_KINDS = {"web_search", "mcp", "function", "openapi",
               "memory_search", "a2a"}


def _req(mapping: dict, key: str, where: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{where}: missing required field {key!r}")
    return mapping[key]


def _parse_tools(raw: list | None, where: str) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    for entry in raw or []:
        if not isinstance(entry, dict) or "type" not in entry:
            raise ConfigError(f"{where}: each tool needs a 'type': {entry!r}")
        kind = str(entry["type"])
        if kind not in _TOOL_KINDS:
            raise ConfigError(
                f"{where}: unknown tool type {kind!r} "
                f"(known: {sorted(_TOOL_KINDS)})")
        fields = {k: v for k, v in entry.items() if k != "type"}
        if kind == "mcp" and "server" not in fields and "server_url" not in fields:
            raise ConfigError(
                f"{where}: an mcp tool needs 'server' (a label from "
                "mcp_servers) or a 'server_url'")
        if kind == "function":
            for f in ("name", "parameters"):
                if f not in fields:
                    raise ConfigError(
                        f"{where}: a function tool needs {f!r}: {entry!r}")
        if kind == "openapi" and "spec" not in fields and "spec_file" not in fields:
            raise ConfigError(
                f"{where}: an openapi tool needs 'spec' (inline) or "
                "'spec_file' (path to a JSON file)")
        tools.append(ToolSpec(kind=kind, fields=fields))
    return tools


def _parse_trigger(raw: dict, where: str) -> TriggerSpec:
    kind = str(_req(raw, "type", where))
    if kind == "schedule":
        cron = str(_req(raw, "cron", where))
        time_zone = str(_req(raw, "time_zone", where))
        _validate_cron(cron, time_zone, where)
        return TriggerSpec(kind=kind, cron=cron, time_zone=time_zone)
    if kind == "timer":
        return TriggerSpec(kind=kind, at=str(_req(raw, "at", where)))
    if kind == "teams":
        return TriggerSpec(
            kind=kind,
            connection=str(_req(raw, "connection", where)),
            group_id=str(_req(raw, "group_id", where)),
            channel_id=str(_req(raw, "channel_id", where)),
        )
    if kind == "github_issue":
        return TriggerSpec(
            kind=kind,
            connection=str(_req(raw, "connection", where)),
            owner=str(_req(raw, "owner", where)),
            repository=str(_req(raw, "repository", where)),
            issue_event=str(raw.get("issue_event", "opened")),
        )
    raise ConfigError(
        f"{where}: unknown trigger type {kind!r} "
        "(known: schedule, timer, teams, github_issue)")


def _validate_cron(cron: str, time_zone: str, where: str) -> None:
    if not time_zone or not str(time_zone).strip():
        raise ConfigError(f"{where}: a schedule trigger needs a time_zone "
                          "(IANA, e.g. 'America/New_York')")
    if not _CRON_RE.match(cron or ""):
        raise ConfigError(f"{where}: cron needs 5 fields, got {cron!r}")
    minute = cron.split()[0]
    if minute == "*":
        raise ConfigError(
            f"{where}: cron {cron!r} fires every minute — the minimum "
            f"interval is {_MIN_INTERVAL_MINUTES} minutes")
    step = re.fullmatch(r"\*/(\d+)", minute)
    if step and int(step.group(1)) < _MIN_INTERVAL_MINUTES:
        raise ConfigError(
            f"{where}: cron {cron!r} fires every {step.group(1)} minutes "
            f"— the minimum interval is {_MIN_INTERVAL_MINUTES} minutes")


def parse_config(data: dict, base_dir: str = ".") -> FoundryConfig:
    """Dict (from YAML) -> validated FoundryConfig."""
    if not isinstance(data, dict):
        raise ConfigError("the config file must be a YAML mapping")
    raw_project = data.get("project") or {}
    project = ProjectSpec(
        endpoint=str(_req(raw_project, "endpoint", "project")),
        chat_model=str(_req(raw_project, "chat_model", "project")),
        embedding_model=str(raw_project.get("embedding_model", "")),
        subscription_id=str(raw_project.get("subscription_id", "") or ""),
        resource_group=str(raw_project.get("resource_group", "") or ""),
    )
    if not project.endpoint.startswith("https://") or "/api/projects/" not in project.endpoint:
        raise ConfigError(
            "project.endpoint must look like "
            "https://<account>.services.ai.azure.com/api/projects/<project>, "
            f"got {project.endpoint!r}")

    agents: list[AgentSpec] = []
    seen: set[str] = set()
    for entry in data.get("agents") or []:
        name = str(_req(entry, "name", "agents[]"))
        if not _AGENT_NAME_RE.match(name):
            raise ConfigError(
                f"agents[]: agent name {name!r} must start with a letter "
                "or digit and use only letters, digits, '_' and '-'")
        if name in seen:
            raise ConfigError(f"agents[]: duplicate agent name {name!r}")
        seen.add(name)
        agents.append(AgentSpec(
            name=name,
            instructions=str(_req(entry, "instructions", f"agent {name!r}")),
            model=str(entry.get("model", "") or project.chat_model),
            tools=_parse_tools(entry.get("tools"), f"agent {name!r}"),
        ))
    if not agents:
        raise ConfigError("agents: at least one agent is required")

    memory = None
    raw_memory = data.get("memory")
    if raw_memory:
        memory = MemorySpec(
            store_name=str(_req(raw_memory, "store_name", "memory")),
            chat_model=str(raw_memory.get("chat_model", "") or project.chat_model),
            embedding_model=str(raw_memory.get("embedding_model", "")
                                or project.embedding_model),
            user_profile=bool(raw_memory.get("user_profile", True)),
            chat_summary=bool(raw_memory.get("chat_summary", True)),
            attach_to=[str(x) for x in (raw_memory.get("attach_to") or [])],
        )
        if not memory.embedding_model:
            raise ConfigError(
                "memory: an embedding_model is required (set it under "
                "memory or project)")

    orchestration = None
    raw_orch = data.get("orchestration")
    if raw_orch:
        mode = str(raw_orch.get("mode", "sequential"))
        if mode not in ("sequential", "a2a"):
            raise ConfigError(
                f"orchestration: mode must be 'sequential' or 'a2a', "
                f"got {mode!r}")
        orchestration = OrchestrationSpec(
            mode=mode,
            parent=str(raw_orch.get("parent", "leader")),
            children=[str(x) for x in (raw_orch.get("children") or [])],
            steps=[str(x) for x in (raw_orch.get("steps") or [])],
        )

    routines: list[RoutineSpec] = []
    for entry in data.get("routines") or []:
        routines.append(RoutineSpec(
            name=str(_req(entry, "name", "routines[]")),
            agent=str(_req(entry, "agent", "routines[]")),
            trigger=_parse_trigger(
                _req(entry, "trigger", f"routine {entry.get('name', '?')!r}"),
                f"routine {entry.get('name', '?')!r}"),
            prompt=str(entry.get("prompt", "")),
            description=str(entry.get("description", "")),
            enabled=bool(entry.get("enabled", True)),
        ))

    mcp_servers: list[McpServerSpec] = []
    seen_labels: set[str] = set()
    for entry in data.get("mcp_servers") or []:
        label = str(_req(entry, "label", "mcp_servers[]"))
        if label in seen_labels:
            raise ConfigError(f"mcp_servers[]: duplicate label {label!r}")
        seen_labels.add(label)
        mcp_servers.append(McpServerSpec(
            label=label,
            url=str(_req(entry, "url", f"mcp server {label!r}")),
            allowed_tools=[str(x) for x in (entry.get("allowed_tools") or [])],
            require_approval=str(entry.get("require_approval", "never")),
            headers=dict(entry.get("headers") or {}),
        ))

    cfg = FoundryConfig(
        project=project, agents=agents, memory=memory,
        orchestration=orchestration, routines=routines,
        mcp_servers=mcp_servers,
    )
    _validate_references(cfg)
    return cfg


def _validate_references(cfg: FoundryConfig) -> None:
    names = cfg.agent_names()
    # Orchestration references.
    if cfg.orchestration:
        o = cfg.orchestration
        if o.mode == "a2a" and (not cfg.project.subscription_id
                                or not cfg.project.resource_group):
            raise ConfigError(
                "orchestration mode 'a2a' needs project.subscription_id "
                "and project.resource_group (the A2A connections are ARM "
                "management-plane resources)")
        if o.parent not in names:
            raise ConfigError(
                f"orchestration: parent {o.parent!r} is not an agent in "
                "this config")
        for child in o.children:
            if child not in names:
                raise ConfigError(
                    f"orchestration: child {child!r} is not an agent in "
                    "this config")
        if o.parent in o.children:
            raise ConfigError(
                f"orchestration: parent {o.parent!r} cannot also be a child")
        for step in o.steps:
            if step not in o.children:
                raise ConfigError(
                    f"orchestration: step {step!r} is not one of the "
                    "children")
        if len(o.steps) > 3:
            raise ConfigError(
                f"orchestration: at most 3 steps (the Leader's cap from "
                f"01), got {len(o.steps)}")
        if o.steps and len(set(o.steps)) != len(o.steps):
            raise ConfigError("orchestration: steps must not repeat an agent")
    # Memory attach targets.
    if cfg.memory:
        for name in cfg.memory.attach_to:
            if name not in names:
                raise ConfigError(
                    f"memory.attach_to: {name!r} is not an agent in this "
                    "config")
    # Routine agents.
    for r in cfg.routines:
        if r.agent not in names:
            raise ConfigError(
                f"routine {r.name!r}: agent {r.agent!r} is not an agent "
                "in this config")
        if r.trigger.kind in ("schedule", "timer") and not r.prompt:
            raise ConfigError(
                f"routine {r.name!r}: a schedule/timer routine needs a "
                "'prompt' (what the agent runs each time)")
    # MCP references from agent tools.
    labels = {s.label for s in cfg.mcp_servers}
    for a in cfg.agents:
        for t in a.tools:
            if t.kind == "mcp" and "server" in t.fields:
                if t.fields["server"] not in labels:
                    raise ConfigError(
                        f"agent {a.name!r}: mcp tool references server "
                        f"{t.fields['server']!r}, which is not in "
                        "mcp_servers")


def load_config(path: str) -> FoundryConfig:
    """Read a foundry.yaml file -> validated FoundryConfig."""
    with open(path) as f:
        text = f.read()
    try:
        data = load_yaml_text(text)
    except YamlSubsetError as exc:
        raise ConfigError(
            f"{path}: YAML parse failed ({exc}). The subset parser only "
            "handles the config's documented shape — install PyYAML "
            "(`pip install pyyaml`) for full YAML.") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the config must be a YAML mapping")
    return parse_config(data, base_dir=os.path.dirname(os.path.abspath(path)))
