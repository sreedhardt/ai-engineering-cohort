"""Ask one question through the guarded pipeline and show what each layer decided.

    uv run python -m evalguard.gateway.cli --role nurse "What is the hand hygiene procedure?"

This is the operator's view: unlike the HTTP gateway it prints the internal
guardrail reasons, so it is for debugging, not for end users.
"""

from __future__ import annotations

import argparse
import json

from evalguard.gateway.pipeline import guarded_chat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--role", required=True,
                        choices=["doctor", "nurse", "billing_executive", "technician", "admin"])
    args = parser.parse_args()

    resp = guarded_chat(args.question, args.role)
    print(f"request_id : {resp.request_id}")
    print(f"route      : {resp.retrieval_type}")
    print(f"blocked    : {resp.blocked}" + (f" (at {resp.blocked_stage})" if resp.blocked else ""))
    for decision in (resp.input_decision, resp.output_decision):
        if decision is None:
            continue
        print(f"\n{decision.stage} guardrail → {decision.verdict.value}")
        for c in decision.checks:
            flag = " [fail-closed]" if c.failed_closed else ""
            print(f"  {c.name:<18} {c.verdict.value:<5}{flag} {c.reason[:160]}")
    print(f"\nuser sees  : {resp.answer}")
    if resp.sources:
        print("sources    : " + json.dumps(resp.sources))
    print(f"latency    : {resp.latency_seconds:.2f}s   tokens: {resp.usage.get('total_tokens', 0)}")


if __name__ == "__main__":
    main()
