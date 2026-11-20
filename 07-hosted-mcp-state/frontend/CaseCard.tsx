/**
 * CaseCard — the two-way card.
 *
 * It shows one case (id, title, current status) with four buttons.
 * Each button answers the agent with a DIFFERENT structured decision:
 *
 *   Approve       -> { action: "set_status", case_id, status: "approved" }
 *   Deny          -> { action: "set_status", case_id, status: "denied" }
 *   Set in review -> { action: "set_status", case_id, status: "in_review" }
 *   Escalate      -> { action: "set_status", case_id, status: "escalated" }
 *
 * The case_id travels in the decision, so the agent knows exactly
 * which case the click belongs to. The thread is implicit: the card
 * answers the tool call that is paused in THIS run, in THIS thread —
 * the decision lands back in the same conversation, never a
 * different one.
 *
 * THE CONTRACT (keep both sides in sync):
 *   - The agent calls the `ask_case_decision` tool with
 *     { case_id, title, current_status } (see registry-snippet.tsx).
 *   - The button's decision is the tool's RESULT. The agent reads
 *     it and calls the real `set_case_status` MCP tool with the
 *     case_id and status from the decision (see the instructions in
 *     foundry/attach.py).
 *   - The card itself never calls the MCP server and never touches
 *     state. It only reports what the human picked.
 */

import { useState } from "react";

export type CaseStatus = "open" | "in_review" | "approved" | "denied" | "escalated";

export interface CaseDecision {
  action: "set_status";
  case_id: string;
  status: CaseStatus;
}

export interface CaseCardData {
  case_id: string;
  title: string;
  current_status: CaseStatus;
}

const VALID_STATUSES: CaseStatus[] = [
  "open",
  "in_review",
  "approved",
  "denied",
  "escalated",
];

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isCaseStatus(v: unknown): v is CaseStatus {
  return typeof v === "string" && (VALID_STATUSES as string[]).includes(v);
}

/**
 * Parse and validate the tool's arguments. Returns null when the
 * shape is wrong, so the registry can show an error instead of
 * crashing. Accepts an object (what CopilotKit passes as `args`)
 * or a JSON string.
 */
export function parseCaseCardData(args: unknown): CaseCardData | null {
  let data: unknown = args;
  if (typeof args === "string") {
    try {
      data = JSON.parse(args);
    } catch {
      return null;
    }
  }
  if (!isRecord(data)) return null;
  if (typeof data.case_id !== "string") return null;
  if (typeof data.title !== "string") return null;
  if (!isCaseStatus(data.current_status)) return null;
  return data as unknown as CaseCardData;
}

const BUTTONS: { label: string; status: CaseStatus }[] = [
  { label: "Approve", status: "approved" },
  { label: "Deny", status: "denied" },
  { label: "Set in review", status: "in_review" },
  { label: "Escalate", status: "escalated" },
];

export function CaseCard({
  data,
  onDecision,
}: {
  data: CaseCardData;
  /** Called with the decision when a button is clicked. In the
   *  registry this is CopilotKit's `respond` — the decision goes
   *  back to the agent as the tool's result. */
  onDecision: (decision: CaseDecision) => void | Promise<void>;
}) {
  const [sent, setSent] = useState<CaseStatus | null>(null);

  const decide = (status: CaseStatus) => {
    if (sent) return; // one decision per card — no double-sends.
    setSent(status);
    void onDecision({ action: "set_status", case_id: data.case_id, status });
  };

  return (
    <div className="case-card">
      <h3>{data.case_id}</h3>
      <p className="case-title">{data.title}</p>
      <p>
        Status: <span className={`case-status case-status-${data.current_status}`}>{data.current_status}</span>
      </p>
      {sent ? (
        <p className="case-sent">Sent: set {data.case_id} to {sent}.</p>
      ) : (
        <div className="case-buttons">
          {BUTTONS.map((b) => (
            <button key={b.status} onClick={() => decide(b.status)}>
              {b.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
