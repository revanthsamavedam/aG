"""Demo — the whole 05 story, offline, no Azure, no key.

  python demo.py

Loads foundry.yaml, shows the plan the provisioner would run, then
applies it to the fake Foundry client and prints what was created:
the six agents (five specialists + Leader), the memory store, the two
routines, and the generated sequential workflow file. The fake keeps
an audit trail of every call, so you can see exactly what the real
provisioner would send to Foundry.
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from config import load_config  # noqa: E402
from fake_foundry import FakeProjectClient  # noqa: E402
from provision import FoundryProvisioner  # noqa: E402


def main() -> int:
    config = load_config(os.path.join(HERE, "foundry.yaml"))
    print("Config: foundry.yaml")
    print(f"  project: {config.project.endpoint}")
    print(f"  agents: {', '.join(a.name for a in config.agents)}")
    print(f"  orchestration: {config.orchestration.mode} "
          f"(parent={config.orchestration.parent}, "
          f"steps={', '.join(config.orchestration.steps)})")
    print(f"  routines: {', '.join(r.name for r in config.routines)}")
    print()

    client = FakeProjectClient(config.project.endpoint)
    provisioner = FoundryProvisioner(client, config.project.endpoint)

    print("Plan (dry run — nothing changes):")
    provisioner.apply(config, dry_run=True)
    print()

    with tempfile.TemporaryDirectory() as tmp:
        print("Applying to the fake Foundry client ...")
        done = provisioner.apply(config, out_dir=tmp)
        print()
        print(f"Applied {len(done)} steps:")
        for step in done:
            print(f"  [{step['kind']}] {step['name']}: {step['action']}")
        print()
        print("What the fake recorded:")
        for call in client.calls:
            op = call["op"]
            if op == "agents.create_version":
                tools = [t["type"] for t in call["definition"]["tools"]]
                print(f"  agent {call['agent_name']!r}: "
                      f"model={call['definition']['model']} tools={tools}")
            elif op == "beta.memory_stores.create":
                print(f"  memory store {call['name']!r}: "
                      f"embedding={call['definition']['embedding_model']}")
            elif op == "beta.routines.create_or_update":
                trigger = call["triggers"]["trigger"]
                print(f"  routine {call['routine_name']!r}: "
                      f"agent={call['action']['agent_name']} "
                      f"trigger={trigger['type']}")
            else:
                print(f"  {op}: {call}")
        workflow = os.path.join(tmp, "workflow.py")
        print()
        print(f"Generated workflow: {workflow}")
        print("  (a temporary copy — run the provisioner with --apply to "
              "write it next to your config)")
        with open(workflow) as f:
            print("  " + "\n  ".join(f.read().splitlines()[:12]))
        print("  ...")
    print()
    print("Next: read MANUAL.md to do this by hand in the portal, or run")
    print("  python provision.py --config foundry.yaml --plan")
    print("against your real project (needs FOUNDRY_PROJECT_ENDPOINT + "
          "`az login`).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
