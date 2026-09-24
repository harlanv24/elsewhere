"""One synthetic Jev request; no campaign state or credentials are printed."""
from __future__ import annotations

import json

from worldsim.jev_client import JevClient, JevClientError, JevConfig


def main() -> int:
    try:
        client = JevClient(JevConfig.from_env())
        decision = client.choose_graph_action({
            "action": "I put the journal into my bag.",
            "current_state": {"description": "A journal lies on the table."},
            "visible_objects": ["journal"],
            "operations": [{"id": "take:journal", "action": "take journal", "label": "Take journal"}],
            "available_actions": [{"id": "take-journal", "label": "Take journal", "operation_id": "take:journal"}],
        })
        print(json.dumps({"decision": decision, "model": client.last_model,
                          "raw_choice": client.last_choice, "status": client.last_status,
                          "confidence": client.last_confidence, "requests": client.requests,
                          "input_tokens": client.input_tokens, "output_tokens": client.output_tokens}))
        return 0 if decision == {"edge_id": "take-journal", "expand": False} else 1
    except (JevClientError, ValueError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
