"""Wiring check: the agent tool's output must match the RiskCard contract.

The frontend's RiskCard (frontend/src/components/RiskCard.tsx) parses the
tool's JSON with parseRiskCardData(), which enforces exactly this shape:
  company: str, risk_level in {Low, Medium, High, Critical},
  summary: str, risks: [{title: str, detail: str}], sources: [str].

This script calls the real show_risk_card tool for every demo company
(and an unknown one) and asserts the same rules, so both sides agree.

Run:  python test_wiring.py
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from risk_data import RISK_DATA  # noqa: E402
from server import show_risk_card  # noqa: E402

VALID_LEVELS = {"Low", "Medium", "High", "Critical"}


def check_shape(data: dict, company: str) -> None:
    assert isinstance(data.get("company"), str) and data["company"], f"{company}: bad company"
    assert data.get("risk_level") in VALID_LEVELS, f"{company}: bad risk_level {data.get('risk_level')!r}"
    assert isinstance(data.get("summary"), str) and data["summary"], f"{company}: bad summary"
    assert isinstance(data.get("risks"), list), f"{company}: risks is not a list"
    for risk in data["risks"]:
        assert isinstance(risk.get("title"), str) and risk["title"], f"{company}: bad risk title"
        assert isinstance(risk.get("detail"), str) and risk["detail"], f"{company}: bad risk detail"
    assert isinstance(data.get("sources"), list) and all(
        isinstance(s, str) for s in data["sources"]
    ), f"{company}: bad sources"


def main() -> None:
    for key in list(RISK_DATA) + ["Some Unknown Corp"]:
        raw = show_risk_card(RISK_DATA[key]["company"] if key in RISK_DATA else key)
        data = json.loads(raw)  # must be a JSON string, as the frontend parses it
        check_shape(data, key)
        print(f"ok: {data['company']} ({data['risk_level']}, {len(data['risks'])} risks)")
    print("RiskCard wiring: all shapes match the frontend contract.")


if __name__ == "__main__":
    main()
