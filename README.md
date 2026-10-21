# Strands IDE

A full code editor backed by a **Strands Agents** agent running on the **Amazon
Bedrock AgentCore** runtime. The agent runtime ("the server") runs locally; the
IDE invokes it over HTTP. The agent has robust tooling for editing a Python
application, and the LLM is the AgentCore-provided Amazon Bedrock model.

```
┌─────────────────┐   POST /invocations    ┌──────────────────────────────┐
│   IDE (browser) │ ──────────────────────▶│  AgentCore runtime (agent/)   │
│  Monaco editor  │ ◀──── JSON response ────│  Strands Agent + Bedrock LLM  │
│  + file tree    │                         │  tools: read/write/edit/shell │
│  + chat panel   │                         │        /python_repl/list/grep │
└────────┬────────┘                         └──────────────────────────────┘
         │  files on the shared local workspace (workspace/)
         ▼
   the user and the agent edit the same files on disk
```

## What's here

| Path | What |
|------|------|
| `agent/` | The AgentCore app (the server). `python -m agent.agent` serves `POST /invocations` and `GET /ping` on `:8080`. |
| `ide/` | The IDE. `python -m ide.server` serves a Monaco web editor + file APIs + chat proxy on `:3000`. |
| `workspace/` | A sample Flask app with pytest tests that the agent reads, edits, and runs. |
| `run.sh` | Starts both processes (separate, as designed). |

## Architecture notes

- **Separation of concerns.** The agent is a standalone, deployable AgentCore
  app — it knows nothing about the IDE. The IDE is just a client that calls the
  runtime's HTTP API and shows the result. This matches the intended usage
  mode: an AgentCore dev brings the server; the IDE invokes the agentic runtime
  running locally.
- **LLM from AgentCore.** The Strands agent uses Amazon Bedrock as its model
  provider (the AgentCore-provided LLM). No model is bundled locally.
- **Shared workspace.** Both processes point at the same `WORKSPACE_DIR`. Edits
  the user makes in the editor and edits the agent makes with its tools land in
  the same files; the IDE refreshes the tree (and any open changed files) after
  each agent response and polls for external changes.
- **Everything runs locally for testing.** The agent code, its tools, and the
  IDE all run on your machine. The only thing that leaves the machine is the
  LLM call to Amazon Bedrock.

## Setup

Requirements: Python 3.10+ (3.12 recommended), and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1) Create a venv and install both the agent and IDE dependencies
uv venv --python 3.12
uv pip install -r requirements.txt

# 2) Give the agent a way to call Bedrock (the AgentCore-provided LLM).
#    Quickest for local dev is a Bedrock API key:
export AWS_BEARER_TOKEN_BEDROCK="<your bedrock api key>"
#    ...or standard AWS credentials, or `aws configure`. Enable the model you
#    use in the Amazon Bedrock console.

# 3) Start both processes
./run.sh            # foreground, logs prefixed [agent] / [ide]
# ./run.sh --bg     # background (logs in .agent.log / .ide.log)

# 4) Open the IDE
open http://localhost:3000
```

### Running the parts individually

```bash
# Agent runtime (the server the AgentCore dev brings)
python -m agent.agent            # serves http://localhost:8080

# IDE (invokes the runtime locally)
python -m ide.server             # serves http://localhost:3000
```

Smoke-test the agent runtime directly (before opening the IDE):

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Read app.py and summarize what the endpoints do."}'
```

## Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `WORKSPACE_DIR` | `./workspace` | Directory the agent and IDE operate on. |
| `BEDROCK_MODEL_ID` | Strands default | Pin a specific Bedrock model id. |
| `AWS_BEARER_TOKEN_BEDROCK` | — | Bedrock API key (quickest local auth). |
| `AGENT_URL` | `http://localhost:8080` | Where the IDE finds the agent runtime. |
| `AGENT_PORT` | `8080` | Port the agent runtime serves on. |
| `IDE_PORT` | `3000` | Port the IDE serves on. |

## Agent tools (robust Python-editing support)

From `strands_tools`: `file_read`, `file_write`, `editor` (view / str_replace /
insert), `shell` (run python, pytest, ruff, git…), `python_repl`.

Custom (`agent/tools.py`): `list_files` (bounded project tree), `grep_files`
(regex content search).

The agent's system prompt directs it to read before editing, make surgical
edits, run/verify changes, and follow the project's style.

## Security note

The agent runs shell commands and file operations **on your local machine**.
That is intentional for local testing, but treat it like giving someone a
shell in the workspace. Don't point it at a workspace with secrets, and don't
expose the IDE/agent ports beyond your machine.
