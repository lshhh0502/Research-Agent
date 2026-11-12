# Research assistant tool-use evaluation

`data/research_tool_eval.jsonl` contains 12 synthetic GAIA-style cases.
Send only `Question` to the agent. `answer` is for separate answer scoring.
The runner creates a temporary workspace, starts local stdio MCP fixtures and
records calls from the real Agent tool loop. The fixtures use synthetic data,
with a fixed evaluation prompt and a limited tool set.

From the repository root, with the model and endpoint configured in `.env`:

```sh
python -m agents.run_tool_eval --case ra-tool-08 --output tool-smoke.jsonl
python -m agents.eval_tool_calls --results tool-smoke.jsonl
python -m agents.run_tool_eval --repeat 3 --output tool-full.jsonl
python -m agents.eval_tool_calls --results tool-full.jsonl
```

Each output path must be new. Use `--model` or `--api-base` to override
configuration. Single-case runs intentionally report other cases as missing.
The runner uses a response marker for clarification/error classification;
check the saved answer text when verifying that the marker is accurate.

For custom runs, collect records in the same format.

Record one JSON line per run, with calls ordered by model request:

```json
{"task_id":"ra-tool-08","run_id":"trial-1","calls":[{"tool":"mcp__reading_notes__search","args":{"query":"evidence cards"},"server":"reading_notes","dispatched":true,"status":"success"}]}
```

Use `server: null` for local calls and for calls that were not dispatched.
For MCP calls, record the actual receiving server from dispatch or server logs,
rather than inferring it from the model's tool name. Preserve blocked attempts.
Allowed statuses are `success`, `tool_error`, `transport_error`, and `blocked`.
A run with no attempts has `"calls":[]`. For clarification or expected tool
errors, add `response_kind` as `clarification` or `tool_error` only after
checking the agent's response. In the tool-error case the response must report
the failure without claiming the record was retrieved.

Expected arguments are a typed subset of actual arguments. Search queries
ignore case and whitespace; other strings match exactly. Expected alternatives
are interchangeable. A corrected later call can pass the case without passing
the first call. Exclusive cases fail if an unapproved tool/server pair was
actually dispatched. Missing cases are reported separately from failed cases.
If a required test server cannot start, record that separately and rerun the
case rather than submitting an empty call record.

The scorer consumes observed JSONL. It does not launch models or MCP servers,
and folding evaluation output does not supply these structured call records.
