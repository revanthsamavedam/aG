/**
 * RiskCard — the example custom component.
 *
 * It renders a company risk summary from structured JSON. The JSON comes
 * from the agent's `show_risk_card` tool (see agent/server.py): the tool
 * returns a JSON string, CopilotKit hands it to this component as the
 * tool-call `result`, and this component parses and renders it.
 *
 * THE CONTRACT (keep both sides in sync):
 *   {
 *     "company": "Anthropic",
 *     "risk_level": "High",            // Low | Medium | High | Critical
 *     "summary": "One or two sentences.",
 *     "risks": [{ "title": "...", "detail": "..." }],
 *     "sources": ["https://..."]
 *   }
 */

export interface RiskItem {
  title: string;
  detail: string;
}

export interface RiskCardData {
  company: string;
  risk_level: "Low" | "Medium" | "High" | "Critical";
  summary: string;
  risks: RiskItem[];
  sources: string[];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Parse and validate whatever the tool returned. Returns null when the
 * shape is wrong, so the registry can show an error instead of crashing.
 * Accepts a JSON string (what CopilotKit passes as `result`) or an object.
 */
export function parseRiskCardData(result: unknown): RiskCardData | null {
  let data: unknown = result;
  if (typeof result === "string") {
    try {
      data = JSON.parse(result);
    } catch {
      return null;
    }
  }
  if (!isRecord(data)) return null;
  if (typeof data.company !== "string") return null;
  if (
    data.risk_level !== "Low" &&
    data.risk_level !== "Medium" &&
    data.risk_level !== "High" &&
    data.risk_level !== "Critical"
  ) {
    return null;
  }
  if (typeof data.summary !== "string") return null;
  if (!Array.isArray(data.risks)) return null;
  for (const r of data.risks) {
    if (!isRecord(r) || typeof r.title !== "string" || typeof r.detail !== "string") {
      return null;
    }
  }
  if (!Array.isArray(data.sources) || !data.sources.every((s) => typeof s === "string")) {
    return null;
  }
  return data as unknown as RiskCardData;
}

export function RiskCard({ data }: { data: RiskCardData }) {
  const levelClass = `risk-level risk-level-${data.risk_level.toLowerCase()}`;
  return (
    <div className="risk-card">
      <h3>{data.company}</h3>
      <span className={levelClass}>{data.risk_level} risk</span>
      <p className="risk-summary">{data.summary}</p>
      <ul>
        {data.risks.map((r) => (
          <li key={r.title}>
            <strong>{r.title}</strong>
            {r.detail}
          </li>
        ))}
      </ul>
      {data.sources.length > 0 && (
        <div className="risk-sources">
          Sources:{" "}
          {data.sources.map((s, i) => (
            <span key={s}>
              {i > 0 ? ", " : ""}
              <a href={s} target="_blank" rel="noreferrer">
                {s}
              </a>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
