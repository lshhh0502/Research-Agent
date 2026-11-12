"""Score observed research-agent tool calls against a JSONL dataset."""

import argparse
from collections import Counter
import json
from pathlib import Path


def load_jsonl(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def matches_args(observed, expected):
    for key, value in expected.items():
        actual = observed.get(key)
        if type(actual) is not type(value):
            return False
        if key == "query" and isinstance(value, str):
            if " ".join(actual.split()).casefold() != " ".join(value.split()).casefold():
                return False
        elif actual != value:
            return False
    return True


def call_failure(call, expected):
    if call["tool"] != expected["tool"]:
        return "wrong_tool"
    if not matches_args(call["args"], expected["args"]):
        return "wrong_args"
    if not call["dispatched"]:
        return "not_dispatched"
    if call.get("server") != expected["server"]:
        return "wrong_server"
    if call["status"] != expected["status"]:
        return "unexpected_result"
    return None


def evaluate(case, run):
    rules = case["tool_expectation"]
    calls = run["calls"]
    mode = rules["mode"]
    first = None
    if mode in {"no_call", "clarify"}:
        reasons = ["unexpected_call"] if calls else []
        if mode == "clarify" and run.get("response_kind") != "clarification":
            reasons.append("missing_clarification")
    else:
        # Pick the closest acceptable alternative to diagnose each attempted call.
        stages = ["wrong_tool", "wrong_args", "not_dispatched", "wrong_server", "unexpected_result", None]
        errors = [
            max((call_failure(call, expected) for expected in rules["any_of"]), key=stages.index)
            for call in calls
        ]
        first = bool(errors) and errors[0] is None
        reasons = []
        if not errors:
            reasons.append("missing_call")
        elif None not in errors:
            reasons.append(max(errors, key=stages.index))
        if rules.get("forbid_other_calls") and any(
            call["dispatched"] and not any(
                call["tool"] == expected["tool"] and call.get("server") == expected["server"]
                for expected in rules["any_of"]
            ) for call in calls
        ):
            reasons.append("forbidden_call")
        if rules.get("expected_response_kind") and run.get("response_kind") != rules["expected_response_kind"]:
            reasons.append("unexpected_response")
    return {
        "task_id": case["task_id"],
        "run_id": run["run_id"],
        "tool_pass": not reasons,
        "first_call_pass": first,
        "failure_reasons": reasons,
    }


def score(cases, runs):
    by_id = {case["task_id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("duplicate task_id in cases")
    seen = set()
    results = []
    for run in runs:
        task_id, run_id = run["task_id"], run["run_id"]
        if task_id not in by_id:
            raise ValueError(f"unknown task_id: {task_id}")
        if not isinstance(run_id, str) or not run_id.strip() or (task_id, run_id) in seen:
            raise ValueError(f"invalid or duplicate run_id for {task_id}: {run_id!r}")
        seen.add((task_id, run_id))
        if not isinstance(run["calls"], list) or any(
            not isinstance(call, dict)
            or not isinstance(call.get("tool"), str)
            or not isinstance(call.get("args"), dict)
            or type(call.get("dispatched")) is not bool
            or call.get("status") not in {"success", "tool_error", "transport_error", "blocked"}
            or "server" not in call
            for call in run["calls"]
        ):
            raise ValueError(f"invalid call record: {task_id}/{run_id}")
        results.append(evaluate(by_id[task_id], run))

    first_calls = [row["first_call_pass"] for row in results if row["first_call_pass"] is not None]
    return {
        "tool_pass": {"passed": sum(row["tool_pass"] for row in results), "total": len(results)},
        "first_call_pass": {"passed": sum(first_calls), "total": len(first_calls)},
        "failure_counts": dict(Counter(reason for row in results for reason in row["failure_reasons"])),
        "missing_cases": sorted(set(by_id) - {task_id for task_id, _ in seen}),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("data/research_tool_eval.jsonl"))
    parser.add_argument("--results", type=Path, required=True, help="Observed tool-call JSONL")
    args = parser.parse_args()
    try:
        report = score(load_jsonl(args.cases), load_jsonl(args.results))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
