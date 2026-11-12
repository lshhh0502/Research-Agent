# RA (Research Agent)

RA is a local research assistant for finding literature, comparing evidence, and writing source-backed reports. It searches the web through configured MCP servers, reads local documents, and keeps research records that can be recalled in later conversations. Use the browser workspace for interactive research or the CLI for terminal workflows.

## Quick start

From the repository root, use Python 3.11 or 3.12 and install the locked dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
```

If you already use a Conda environment, activate it and install `requirements.lock` there. The browser workspace uses HTML, CSS, and JavaScript served by the Python backend. Node.js is needed for some MCP servers and frontend tests.

Create a local configuration file if one does not already exist:

```bash
test -f .env || cp .env.example .env
```

Edit `.env` with one of the following configurations. When using provider-specific variables, remove or comment out the active `APIKEY`, `API`, and `MINI_CLAUDE_MODEL` values from the template. Generic credentials and endpoints take precedence over their provider-specific counterparts, and `MODEL` takes precedence over `MINI_CLAUDE_MODEL`.

OpenAI-compatible endpoint:

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-provider.example/v1
MODEL=your-model-id
```

Anthropic-compatible endpoint:

```dotenv
ANTHROPIC_API_KEY=your-api-key
ANTHROPIC_BASE_URL=https://your-provider.example/anthropic
MODEL=your-model-id
```

Start the workspace and open [http://127.0.0.1:8765](http://127.0.0.1:8765):

```bash
python -m agents.web
```

The service binds to loopback for local use. Restart it after editing `.env`. Use `python -m agents.web --port 8766` if the default port is occupied.

The CLI also supports interactive sessions, one-off requests, plans, and session resumption:

```bash
python -m agents.main
python -m agents.main "Compare evaluation methods for retrieval-augmented generation."
python -m agents.main --plan "Plan a literature review on citation verification."
python -m agents.main --resume
python -m agents.main --help
```

The [Dockerfile](Dockerfile) builds the CLI image. To use project research Skills and sub-agents inside the container, make the project's `.bear/` directory available in its working directory. The image does not package the browser workspace or install `uvx`.

## Research workflow

In the browser, choose **Find literature**, **Review the literature**, or **Summarize a paper** to prefill a request. Use `/research` to load the project's [research workflow](.bear/skills/research/SKILL.md). For example:

> /research Compare retrieval quality and answer faithfulness metrics for RAG. Cite the papers you actually inspect, flag disagreements and evidence gaps, and save the findings as a research memory. Write the report in English.

RA can work across web sources and local notes. It tracks search scope and coverage, distinguishes full-text evidence from abstracts, and can link conclusions to supporting and opposing evidence. The main agent consolidates the work before saving it.

| Sub-agent | Role |
| --- | --- |
| `explore` | Explores research directions in read-only mode. One call can run two to four independent directions in parallel. |
| `retriever` | Finds candidate web and local sources and reports relevance, search coverage, and gaps. |
| `analyst` | Extracts evidence, comparison dimensions, and limitations from source material. |
| `verifier` | Checks conclusions against evidence and counterexamples and reports conflicts or gaps. |

The research sub-agents use local read tools and, where allowed, a subset of the parent agent's connected MCP tools.

## MCP tools

The project defines MCP servers in [`.mcp.json`](.mcp.json). Run RA from the repository root to use this configuration.

| Server | Purpose | Requirements |
| --- | --- | --- |
| `fetch` | Retrieve content from a known URL | `uvx` |
| `playwright` | Browse and interact with web pages | Node.js, `npx`, and an available browser |
| `brave-search` | Search the web by query | Node.js, `npx`, and `BRAVE_API_KEY` in the environment or `.env` |
| `arxiv` | Search for arXiv papers | `uvx` |
| `context7` | Look up library documentation | Node.js and `npx` |

Server startup and tool execution have separate permission checks. In the browser, start a new conversation with **Default permissions**, request a specific tool, and review the approval prompts. For example, ask RA to fetch `https://example.com` and report the page heading. Expand the MCP tool entry under **Task details → Activity** to see its result. Plan mode does not start MCP servers.

Project-level allow rules require trusting the current configuration via the CLI command `/trust-project`. Trust is tied to the project path and configuration digest. Deny rules continue to apply after trust is granted.

## Evidence, memory, and Skills

| Record | What RA stores |
| --- | --- |
| Source | ID, title, URL or local path, publication date, and time accessed |
| Evidence | ID, source ID, excerpt, page or section, and applicable conditions |
| Claim | ID, conclusion, supporting and opposing evidence IDs, and verification status |

A project memory of type `research` stores these records together with scope, coverage cutoff, last verification time, open questions, and any superseding record. Validation checks the structure and links between IDs. The research workflow uses source review and the verifier sub-agent to assess what the evidence supports. Later sessions can recall relevant project memories.

Session folding preserves the active research state across long conversations. The structured summary can carry forward the research question, subquestions, prior queries, consulted sources, claim and evidence references, disputes, next actions, and remaining budget. When the fold fails validation, RA retains the original conversation.

Skills capture reusable methods and output preferences. Feedback such as “lead future literature reviews with open questions” can produce a Skill candidate through the online learning pipeline. The CLI command `/skill-eval` starts the replay review workflow. After reviewing a candidate, `/skill-apply <id> --manual-review` records a manual publication decision. Publication and background writes follow the active permission rules. See [runtime and Skill publication details](docs/runtime-hardening.md).

When troubleshooting, check the terminal that launched RA. Saved conversations are under `~/.bear-code/sessions/`. Folding records are under `.bear/sessions/` in the current project.

## Evaluation

The tool-use suite contains 12 synthetic research cases. Its runner exercises the Agent's tool loop with controlled local MCP fixtures, and the scorer evaluates the recorded calls. Configure a model in `.env` before running the suite. Choose a new output path for each run:

```bash
python -m agents.run_tool_eval --case ra-tool-08 --output ra-tool-smoke.jsonl
python -m agents.eval_tool_calls --results ra-tool-smoke.jsonl
```

A single-case run reports the remaining cases as missing. For repeated trials across all cases, use `--repeat 3` without `--case` and score the resulting JSONL. See the [tool evaluation guide](docs/research-tool-eval.md) for record formats and scoring rules.

The folding evaluator runs paired trials with folding enabled and disabled. Validate the smoke dataset first, then run the model-backed comparison:

```bash
python -m agents.eval_folding --cases data/folding_eval_smoke.jsonl --output .bear/folding-eval/ra-smoke --validate-only
python -m agents.eval_folding --cases data/folding_eval_smoke.jsonl --output .bear/folding-eval/ra-smoke --fold-after-turn 2
```

Per-run artifacts include `trace.json` and `console.log`. The repository also includes GAIA and HLE data. Benchmark results depend on the selected cases, model, tools, and evaluation settings. Figures attributed to the [DeepAgent paper](paper/deepAgents.pdf) are published reference results.

## Repository layout

| Path | Responsibility |
| --- | --- |
| [agents/](agents/) | Agent runtime, CLI, web API, tools, MCP, memory, and evaluation |
| [web/](web/) | Browser workspace and frontend tests |
| [.bear/skills/research/](.bear/skills/research/) | Research workflow and evidence record format |
| [.bear/agents/](.bear/agents/) | Research sub-agent definitions |
| [data/](data/) | Evaluation datasets and fixtures |
| [tests/](tests/) | Python regression tests |

Run the Python regression suite and the frontend checks from the repository root. The frontend checks use Node.js and require no npm package installation.

```bash
python -m unittest discover -s tests -q
node --check web/app.js
node --test web/tests/*.test.mjs
```

## Attribution and license

RA adapts and extends the agent runtime from [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch), including its tool, prompt, memory, MCP, and sub-agent foundations. The original copyright and MIT license are included in [LICENSE](LICENSE).
