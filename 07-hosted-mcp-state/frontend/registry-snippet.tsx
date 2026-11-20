/**
 * Registry snippet — add the CaseCard to the 06 scaffold's registry.
 *
 * Drop this block into `useCustomComponents()` in
 * frontend/src/components/registry.tsx (next to the RiskCard
 * registration), and copy CaseCard.tsx into the same components/
 * folder. Import at the top of registry.tsx:
 *
 *   import { useHumanInTheLoop } from "@copilotkit/react-core/v2";
 *   import { CaseCard, parseCaseCardData } from "./CaseCard";
 *
 * WHY useHumanInTheLoop AND NOT useRenderTool:
 * `useRenderTool` only RENDERS a tool call — its render props have no
 * way to answer one (verified in @copilotkit/react-core 1.89.0:
 * RenderToolProps carries no `respond`). `useHumanInTheLoop` is the
 * hook that PAUSES the tool call and hands the render function
 * `respond(result)` — calling it sends the decision back to the agent
 * as the tool's result and resumes the run. That is the two-way half.
 *
 * The tool registered here, `ask_case_decision`, is a FRONTEND tool:
 * it does not run on any server. The agent calls it when a status
 * change needs a human; the card answers; the agent then calls the
 * REAL `set_case_status` MCP tool with the case_id and status from
 * the card's response. The agent's instructions (foundry/attach.py)
 * say exactly that, so the two halves match.
 */

import { useHumanInTheLoop } from "@copilotkit/react-core/v2";
import { z } from "zod";
import { CaseCard, parseCaseCardData } from "./CaseCard";

// --- CaseCard -----------------------------------------------------------
// Agent tool: ask_case_decision({case_id, title, current_status}).
// The card's button click is the tool's RESULT — see CaseCard.tsx.
useHumanInTheLoop(
  {
    name: "ask_case_decision",
    description:
      "Ask the human what to do with a case. Show the case as a card " +
      "with Approve / Deny / Set in review / Escalate buttons, and " +
      "wait for the human to pick one. The result is the decision: " +
      "{action: 'set_status', case_id, status}.",
    parameters: z.object({
      case_id: z.string(),
      title: z.string(),
      current_status: z.enum([
        "open",
        "in_review",
        "approved",
        "denied",
        "escalated",
      ]),
    }),
    render: ({ status, args, respond }) => {
      if (status === "inProgress") {
        return <div className="card-status">Preparing case card…</div>;
      }
      // While executing, `respond` is defined: it sends the decision
      // back as this tool call's result. Once complete it is
      // undefined — the card has already answered.
      if (status === "complete" || !respond) {
        return <div className="card-status">Decision sent.</div>;
      }
      const data = parseCaseCardData(args);
      if (!data) {
        return (
          <div className="card-error">
            The agent sent case data I could not read.
          </div>
        );
      }
      return <CaseCard data={data} onDecision={respond} />;
    },
  },
  [],
);
