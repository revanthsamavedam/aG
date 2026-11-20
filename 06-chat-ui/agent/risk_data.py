"""Fake company risk data for demo mode.

These are small, hand-written demo numbers (from public reporting on the
Anthropic S-1, October 2026). They exist so the scaffold runs with no API
keys and no network. Do NOT treat them as live research — in real mode the
agent researches for real and the tool returns live data in the same shape.
"""

RISK_DATA: dict[str, dict] = {
    "anthropic": {
        "company": "Anthropic",
        "risk_level": "High",
        "summary": (
            "Anthropic filed its S-1 in October 2026 with a risk section over "
            "90 pages long. The biggest risk is compute dependence: $265.6B in "
            "future commitments to suppliers who also build rival models."
        ),
        "risks": [
            {
                "title": "Compute dependence",
                "detail": (
                    "$265.6B in future compute commitments, much of it "
                    "take-or-pay, to Google, Amazon, Microsoft and Broadcom — "
                    "all of whom build competing models."
                ),
            },
            {
                "title": "Governance",
                "detail": (
                    "A single Class F share held by the seven co-founders "
                    "carries 50.1% of voting power; public shareholders get "
                    "the economics but almost no control."
                ),
            },
            {
                "title": "Competition",
                "detail": (
                    "OpenAI (ChatGPT) and Alphabet (Gemini) spend billions to "
                    "stay competitive, and low-cost entrants like DeepSeek can "
                    "reset pricing."
                ),
            },
        ],
        "sources": [
            "https://www.fool.com/investing/2026/10/31/3-major-risks-with-anthropics-ipo/",
            "https://www.morningstar.com/stocks/anthropics-s-1-risks-humanity-other-warnings",
        ],
    },
    "openai": {
        "company": "OpenAI",
        "risk_level": "Medium",
        "summary": (
            "Demo data: OpenAI is the market leader in chatbots, but its "
            "business is capital-intensive and its governance has been "
            "publicly contested."
        ),
        "risks": [
            {
                "title": "Compute cost",
                "detail": (
                    "Training and serving frontier models requires tens of "
                    "billions in compute spend, mostly rented."
                ),
            },
            {
                "title": "Governance",
                "detail": (
                    "The nonprofit-board structure has produced public "
                    "leadership crises before."
                ),
            },
        ],
        "sources": ["https://openai.com/"],
    },
}


def lookup(company: str) -> dict:
    """Return the risk card dict for a company, or a generic card."""
    key = (company or "").strip().lower()
    if key in RISK_DATA:
        return RISK_DATA[key]
    return {
        "company": company.strip() or "Unknown",
        "risk_level": "Medium",
        "summary": (
            "No demo data for this company. In real mode the agent "
            "researches the company and returns live data in this same shape."
        ),
        "risks": [],
        "sources": [],
    }
