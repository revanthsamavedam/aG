/**
 * THE REGISTRY — the one place custom components live.
 *
 * A custom component is a React component the agent can trigger: the agent
 * calls a tool, and instead of plain text the chat shows your component.
 *
 * To register a new component, 3 steps (see README for the full walkthrough):
 *
 *   1. Write the React component (e.g. src/components/MyCard.tsx).
 *   2. Register it HERE with `useRenderTool`: same `name` as the agent's
 *      tool, a `parameters` schema for the tool's arguments, and a `render`
 *      function that turns the tool's `result` into your component.
 *   3. Give the agent a tool with the SAME name (agent/server.py). The tool
 *      returns a JSON string; `render` receives it as `result`.
 *
 * How it flows: user asks a question -> agent calls the tool -> the tool
 * runs on the server and returns JSON -> CopilotKit calls `render` with
 * { status, parameters, result } -> your component renders in the chat.
 *
 * `status` is "inProgress" (agent is deciding), "executing" (tool running),
 * or "complete" (result is in). `parameters` is the tool's arguments,
 * typed by the zod schema. `result` is the tool's return value as a string.
 */

import { useRenderTool } from "@copilotkit/react-core/v2";
import { z } from "zod";
import { RiskCard, parseRiskCardData } from "./RiskCard";

export function useCustomComponents() {
  // --- RiskCard -----------------------------------------------------------
  // Agent tool: show_risk_card(company: str) -> JSON string (see agent/).
  useRenderTool(
    {
      name: "show_risk_card",
      parameters: z.object({ company: z.string() }),
      render: ({ status, parameters, result }) => {
        if (status === "inProgress") {
          return <div className="card-status">Preparing risk card…</div>;
        }
        if (status === "executing") {
          return (
            <div className="card-status">
              Looking up risk for {parameters.company}…
            </div>
          );
        }
        const data = parseRiskCardData(result);
        if (!data) {
          return (
            <div className="card-error">
              The agent returned risk data I could not read.
            </div>
          );
        }
        return <RiskCard data={data} />;
      },
    },
    [],
  );

  // --- Add your next component here, same shape as above. ----------------
  // useRenderTool(
  //   {
  //     name: "my_tool",
  //     parameters: z.object({ my_arg: z.string() }),
  //     render: ({ status, result }) => {
  //       if (status !== "complete") return <div className="card-status">Working…</div>;
  //       return <MyCard data={parseMyData(result)} />;
  //     },
  //   },
  //   [],
  // );
}
