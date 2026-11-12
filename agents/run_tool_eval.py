"""Run the research tool benchmark with isolated stdio MCP fixtures."""

import argparse
import asyncio
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

from agents.agent import Agent
from agents.config import load_env_file, resolve_agent_options, resolve_api_config
from agents.eval_tool_calls import load_jsonl
from agents.mcp_client import McpManager, McpToolError
from agents.tool_status import tool_failed
from agents.tools import tool_definitions

ROOT = Path(__file__).resolve().parents[1]
PAPER = "Evidence Cards for Research Agents"
NOTES = "Structured Notes for Long-Horizon Research"
TOOLS = {
    "literature": {
        "search": ("Search the public literature index.", {"query": "string", "year": "integer", "limit": "integer"}, ["query"]),
        "lookup_doi": ("Retrieve a literature record by its full DOI.", {"doi": "string"}, ["doi"]),
    },
    "reading_notes": {"search": ("Search private reading notes.", {"query": "string"}, ["query"])},
    "web": {"fetch": ("Read an existing web URL.", {"url": "string"}, ["url"])},
    "browser": {"open": ("Open an existing web URL in the browser.", {"url": "string"}, ["url"])},
    "archive": {"find_accession": ("Find the archive accession by paper title.", {"title": "string"}, ["title"])},
}
CURRENT_CALL = ContextVar("evaluation_call")


def fixture_result(server, tool, args):
    if server == "literature" and tool == "search":
        query = " ".join(args["query"].casefold().split())
        if query == "memory folding" or query == NOTES.casefold():
            return NOTES
        if query == "citation grounding" and args.get("year") == 2025:
            return "Citation Grounding in Research Assistants"
    if server == "literature" and tool == "lookup_doi" and args["doi"] == "10.9999/ra.017":
        return PAPER
    if server == "reading_notes" and args["query"].strip().casefold() == "evidence cards":
        return "Evidence Cards Reading Notes"
    if server == "archive" and args["title"] == PAPER:
        return "ARC-017"
    if server in {"web", "browser"} and args["url"] == "https://research.example.test/evidence-cards":
        return "Evidence Cards Project"
    return None


def serve_fixture(server, receipt_path):
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request["method"]
        response = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": server, "version": "1"},
            }
        elif method == "tools/list":
            response["result"] = {"tools": [
                {"name": name, "description": description, "inputSchema": {
                    "type": "object", "properties": {key: {"type": kind} for key, kind in fields.items()},
                    "required": required, "additionalProperties": False,
                }} for name, (description, fields, required) in TOOLS[server].items()
            ]}
        elif method == "tools/call":
            params = request["params"]
            name, args = params["name"], params["arguments"]
            with receipt_path.open("a", encoding="utf-8") as receipt:
                receipt.write(json.dumps({"server": server, "name": name, "args": args}) + "\n")
            value = fixture_result(server, name, args)
            response["result"] = {
                "content": [{"type": "text", "text": value or "No matching record found."}],
                "isError": value is None and name == "lookup_doi",
            }
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
        print(json.dumps(response), flush=True)


class FixtureManager(McpManager):
    def _load_configs(self):
        return {
            server: {"command": sys.executable, "args": [
                str(Path(__file__).resolve()), "--fixture", server, str(Path.cwd() / "receipts.jsonl")
            ], "env": {"PYTHONPATH": str(ROOT)}}
            for server in TOOLS
        }


class RecordedAgent(Agent):
    def __init__(self, **options):
        super().__init__(**options)
        self._mcp_manager = FixtureManager()
        self.calls = []
        self.call_lock = asyncio.Lock()
        self.receipt_count = 0

    async def _run_checked_tool(self, name, inp):
        call = {"tool": name, "args": inp if isinstance(inp, dict) else {},
                "server": None, "dispatched": False, "status": "blocked"}
        self.calls.append(call)
        async with self.call_lock:
            if name in {"read_file", "list_files"} and isinstance(inp, dict):
                raw_path = inp.get("file_path", inp.get("path", "."))
                if isinstance(raw_path, str) and not Path(raw_path).resolve().is_relative_to(Path.cwd()):
                    return "Action denied: evaluation tools may only access the test workspace."
                pattern = inp.get("pattern")
                if isinstance(pattern, str) and ".." in Path(pattern).parts:
                    return "Action denied: glob cannot traverse above the test workspace."
            token = CURRENT_CALL.set(call)
            try:
                return await super()._run_checked_tool(name, inp)
            finally:
                CURRENT_CALL.reset(token)
                receipt_path = Path("receipts.jsonl")
                if name.startswith("mcp__") and receipt_path.exists():
                    receipts = load_jsonl(receipt_path)
                    new_receipts = receipts[self.receipt_count:]
                    self.receipt_count = len(receipts)
                    if new_receipts:
                        call.update(server=new_receipts[-1]["server"], dispatched=True)

    async def _execute_tool_call(self, name, inp):
        call = CURRENT_CALL.get()
        if not name.startswith("mcp__"):
            call["dispatched"] = True
        try:
            result = await super()._execute_tool_call(name, inp)
        except McpToolError:
            call["status"] = "tool_error"
            raise
        except Exception:
            call["status"] = "transport_error" if name.startswith("mcp__") else "tool_error"
            raise
        call["status"] = "tool_error" if tool_failed(name, result) else "success"
        return result


async def run_case(case, run_id, options, timeout):
    original_cwd = Path.cwd()
    with TemporaryDirectory(prefix="bear-tool-eval-") as directory:
        try:
            os.chdir(directory)
            Path("notes").mkdir()
            Path("notes/reading-list.md").write_text(PAPER + "\n", encoding="utf-8")
            agent = RecordedAgent(
                **options, permission_mode="bypassPermissions", enable_folding=False, enable_memory_prefetch=False,
                custom_system_prompt="You are a research assistant. Follow the user’s request and use available tools when needed.",
                enable_online_evolution=False, max_turns=8, max_output_tokens=512,
                custom_tools=[tool for tool in tool_definitions if tool["name"] in {"read_file", "list_files"}],
            )
            try:
                # Preflight before spending a model request.
                await agent._mcp_manager.load_and_connect(authorize_start=agent.authorize_operation)
                definitions = agent._mcp_manager.get_tool_definitions()
                expected = {f"mcp__{server}__{name}" for server, tools in TOOLS.items() for name in tools}
                missing = expected - {tool["name"] for tool in definitions}
                if missing:
                    raise RuntimeError("Fixture tools unavailable: " + ", ".join(sorted(missing)))
                agent.tools += definitions
                agent._mcp_initialized = True
                result = await asyncio.wait_for(agent.run_once(
                    case["Question"] + "\n\nEnd with RESPONSE_KIND: followed by one of "
                    "answer, clarification, or tool_error. Use tool_error if the requested record could not be retrieved.\n"
                    "If a tool returns an error, report it accurately in your answer."
                ), timeout=timeout)
                if agent._aborted:
                    raise RuntimeError("Agent aborted")
            finally:
                await agent.aclose()
            output = result["text"]
            markers = re.findall(r"(?im)^\s*RESPONSE_KIND:\s*(answer|clarification|tool_error)\s*$", output)
            return {"task_id": case["task_id"], "run_id": run_id, "calls": agent.calls,
                    "response_kind": markers[-1].lower() if markers else None, "output": output}
        finally:
            os.chdir(original_cwd)


async def run(args):
    cases = load_jsonl(args.cases)
    if args.case:
        selected = set(args.case)
        unknown = selected - {case["task_id"] for case in cases}
        if unknown:
            raise ValueError("Unknown case IDs: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case["task_id"] in selected]
    load_env_file()
    options = resolve_agent_options(api_config=resolve_api_config(args.api_base), model=args.model)
    if not options["api_key"]:
        raise ValueError("Configure the model API key in .env first.")
    with args.output.open("x", encoding="utf-8") as output:
        for case in cases:
            for trial in range(1, args.repeat + 1):
                print(f"Running {case['task_id']} ({trial}/{args.repeat})", file=sys.stderr)
                record = await run_case(case, f"trial-{trial}", options, args.timeout)
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--fixture":
        serve_fixture(sys.argv[2], Path(sys.argv[3]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "data/research_tool_eval.jsonl")
    parser.add_argument("--case", action="append", help="Run only this task ID (repeatable)")
    parser.add_argument("--output", type=Path, required=True, help="New observed JSONL file")
    parser.add_argument("--model")
    parser.add_argument("--api-base")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0:
        parser.error("--repeat and --timeout must be positive")
    try:
        asyncio.run(run(args))
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        parser.exit(1, f"Evaluation stopped: {exc}\n")


if __name__ == "__main__":
    main()
