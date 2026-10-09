"""Run the deterministic Agent behavior fixture set and print JSON."""

from __future__ import annotations

import json

from novelvideo.chat.agent_eval_fixtures import AGENT_EVAL_CASES
from novelvideo.chat.agent_evals import evaluate_agent_case_set


def main() -> int:
    result = evaluate_agent_case_set(AGENT_EVAL_CASES)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
